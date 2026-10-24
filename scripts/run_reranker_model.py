"""One cached, revision-pinned CPU Qwen reranker; no DB or provider credentials."""

import json
import re
import sys
from importlib.metadata import version

from agent_core.rag.local_models import MAX_FRAME_BYTES
from agent_core.rag.reranking import (
    INSTRUCTION,
    MAX_PAIR_TOKENS,
    MODEL,
    PREFIX,
    RECIPE,
    SUFFIX,
)
from scripts.process_document import watch_parent


def main():
    protocol = sys.stdout
    sys.stdout = sys.stderr
    model, profile = None, None
    for line in iter(lambda: sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1), b""):
        if len(line) > MAX_FRAME_BYTES:
            return
        request_id, request = None, {}
        try:
            request = json.loads(line)
            request_id = request["request_id"]
            if request["operation"] == "load" and model is None:
                watch_parent(request)
                import torch
                from huggingface_hub import HfApi
                from transformers import AutoModelForCausalLM, AutoTokenizer

                torch.set_num_threads(2)
                revision = request["revision"]
                if not re.fullmatch(r"[0-9a-f]{40}", revision):
                    revision = HfApi(token=False).model_info(MODEL, revision=revision).sha
                tokenizer = AutoTokenizer.from_pretrained(
                    MODEL,
                    revision=revision,
                    padding_side="left",
                    trust_remote_code=False,
                    token=False,
                )
                model = AutoModelForCausalLM.from_pretrained(
                    MODEL,
                    revision=revision,
                    trust_remote_code=False,
                    token=False,
                    torch_dtype=torch.float32,
                    use_safetensors=True,
                ).eval()
                no_id, yes_id = (tokenizer.convert_tokens_to_ids(word) for word in ("no", "yes"))
                if no_id == yes_id or any(
                    token is None or token == tokenizer.unk_token_id for token in (no_id, yes_id)
                ):
                    raise ValueError("Invalid yes/no tokens")
                prefix = tokenizer.encode(PREFIX, add_special_tokens=False)
                suffix = tokenizer.encode(SUFFIX, add_special_tokens=False)
                profile = {
                    "model": MODEL,
                    "revision": revision,
                    "recipe": RECIPE,
                    "max_pair_tokens": MAX_PAIR_TOKENS,
                    "dtype": "float32",
                    "instruction": INSTRUCTION,
                    "library": f"transformers-{version('transformers')}",
                }
                result = {"ok": True, "profile": profile}
            elif request["operation"] == "score" and model is not None:
                passages, query, batch_size = (
                    request["passages"],
                    request["query"],
                    request["batch_size"],
                )
                if not 1 <= len(passages) <= 20 or not 1 <= batch_size <= 4:
                    raise ValueError("Invalid rerank batch")
                sequences = [
                    prefix
                    + tokenizer.encode(
                        f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {passage}",
                        add_special_tokens=False,
                    )
                    + suffix
                    for passage in passages
                ]
                if any(len(tokens) > MAX_PAIR_TOKENS for tokens in sequences):
                    result = {
                        "ok": False,
                        "code": "reranker_token_limit",
                        "message": "Cặp câu hỏi/đoạn văn vượt giới hạn token reranker.",
                    }
                else:
                    scores = []
                    with torch.inference_mode():
                        for start in range(0, len(sequences), batch_size):
                            inputs = tokenizer.pad(
                                {"input_ids": sequences[start : start + batch_size]},
                                padding=True,
                                return_tensors="pt",
                                return_attention_mask=True,
                            )
                            # Public Qwen3 API: avoid allocating sequence x vocabulary logits.
                            logits = (
                                model(**inputs, use_cache=False, logits_to_keep=1)
                                .logits[:, -1, :]
                                .float()
                            )
                            scores.extend(
                                torch.softmax(logits[:, [no_id, yes_id]], dim=-1)[:, 1].tolist()
                            )
                            del logits, inputs
                    result = {"ok": True, "scores": scores}
                    del scores
                del sequences, passages, query
            else:
                raise ValueError("Unexpected operation")
        except ModuleNotFoundError:
            result = {
                "ok": False,
                "code": "reranker_dependencies_missing",
                "message": "Máy chủ chưa cài dependency reranker.",
            }
        except (MemoryError, RuntimeError):
            result = {
                "ok": False,
                "code": "reranker_runtime",
                "message": "Reranker không hoàn tất; kiểm tra bộ nhớ/dependency.",
            }
        except Exception:
            result = {
                "ok": False,
                "code": "reranker_unavailable",
                "retryable": True,
                "message": "Chưa tải/chạy được reranker; kiểm tra kết nối/cache.",
            }
        result["request_id"] = request_id
        protocol.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        protocol.flush()
        del request, result, line


if __name__ == "__main__":
    main()
