"""One cached F2LLM CPU model, bounded JSON-line IPC; no DB or provider credentials."""

import json
import sys
from importlib.metadata import version

from agent_core.rag.embeddings import DIMENSIONS, MAX_FRAME_BYTES, MODEL, QUERY_PROMPT, RECIPE
from scripts.process_document import watch_parent


def main():
    protocol = sys.stdout
    sys.stdout = sys.stderr  # Third-party progress/log output cannot corrupt the result protocol.
    model, profile = None, None
    for line in iter(lambda: sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1), b""):
        if len(line) > MAX_FRAME_BYTES:
            return
        request_id = None
        request = {}
        try:
            request = json.loads(line)
            request_id = request["request_id"]
            if request["operation"] == "load" and model is None:
                watch_parent(request)
                import torch
                from sentence_transformers import SentenceTransformer

                torch.set_num_threads(2)
                model = SentenceTransformer(
                    MODEL,
                    revision=request["revision"],
                    device="cpu",
                    trust_remote_code=False,
                    token=False,
                    model_kwargs={"torch_dtype": torch.float32, "use_safetensors": True},
                )
                model.max_seq_length = 2048
                model.eval()
                if model.get_sentence_embedding_dimension() != DIMENSIONS:
                    raise ValueError("Unexpected embedding dimension")
                # SentenceTransformers 5.7 replaces the legacy boolean with pooling_mode.
                if not any(
                    getattr(module, "pooling_mode", None) == "lasttoken"
                    or getattr(module, "pooling_mode_lasttoken", False)
                    for module in model
                ):
                    raise ValueError("F2LLM requires last-token pooling")
                profile = {
                    "model": MODEL,
                    "revision": request["revision"],
                    "recipe": RECIPE,
                    "dimensions": DIMENSIONS,
                    "normalized": True,
                    "dtype": "float32",
                    "library": f"sentence-transformers-{version('sentence-transformers')}",
                }
                result = {"ok": True, "profile": profile}
            elif request["operation"] in {"query", "document"} and model is not None:
                texts = request["texts"]
                is_query = request["operation"] == "query"
                prompt = QUERY_PROMPT if is_query else ""
                max_tokens = 1024 if is_query else 512
                if not 1 <= len(texts) <= 32 or (is_query and len(texts) != 1):
                    raise ValueError("Invalid embedding batch")
                if any(
                    len(model.tokenizer.encode(prompt + text, add_special_tokens=True)) > max_tokens
                    for text in texts
                ):
                    result = {
                        "ok": False,
                        "code": "embedding_token_limit",
                        "message": "Văn bản vượt giới hạn token embedding; cần chia ngắn hơn.",
                        "retryable": False,
                    }
                else:
                    encode = model.encode_query if is_query else model.encode_document
                    vectors = encode(
                        texts,
                        prompt=prompt,
                        normalize_embeddings=True,
                        convert_to_numpy=True,
                        show_progress_bar=False,
                        batch_size=len(texts),
                    )
                    result = {"ok": True, "vectors": vectors.astype("float32").tolist()}
                    del vectors
                del texts
            else:
                raise ValueError("Unexpected model operation")
        except ModuleNotFoundError:
            result = {
                "ok": False,
                "code": "embedding_dependencies_missing",
                "message": "Máy chủ chưa cài dependency embedding.",
                "retryable": False,
            }
        except (MemoryError, RuntimeError):
            result = {
                "ok": False,
                "code": "embedding_runtime",
                "message": "Model embedding không hoàn tất; kiểm tra bộ nhớ và dependency.",
                "retryable": False,
            }
        except Exception:
            result = {
                "ok": False,
                "code": "embedding_model_unavailable",
                "message": "Chưa tải/chạy được model embedding. Kiểm tra kết nối/cache.",
                "retryable": True,
            }
        result["request_id"] = request_id
        protocol.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        protocol.flush()
        del request, result, line


if __name__ == "__main__":
    main()
