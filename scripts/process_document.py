"""Private worker subprocess entrypoint; no DB, LLM, SMTP or user-facing stdout."""

import argparse
import hashlib
import json
import os
import threading
from pathlib import Path

import psutil

from agent_core.rag.documents import ParsedDocument, PreparationError


def write_json(path: Path, payload: dict, max_bytes: int) -> None:
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    size = 0
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        for part in encoder.iterencode(payload):
            block = part.encode("utf-8")
            size += len(block)
            if size > max_bytes:
                raise PreparationError("document_artifact_limit", "Dữ liệu xử lý vượt giới hạn.")
            output.write(block)


def watch_parent(request: dict) -> None:
    """Stop this child and Office descendants if the worker is killed without cleanup."""
    parent = psutil.Process(request["parent_pid"])
    expected = request["parent_created_at"]
    interval = threading.Event()

    def watch():
        while True:
            try:
                if (
                    parent.is_running()
                    and parent.status() != psutil.STATUS_ZOMBIE
                    and parent.create_time() == expected
                ):
                    interval.wait(0.5)
                    continue
            except psutil.NoSuchProcess:
                pass
            try:
                for child in psutil.Process().children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
            finally:
                os._exit(1)

    threading.Thread(target=watch, name="document-worker-watch", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("parse", "chunk"))
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    work = args.request.resolve().parent
    request = json.loads(args.request.read_text(encoding="utf-8"))
    watch_parent(request)
    options = request["options"]
    result = {"ok": False}
    try:
        if args.phase == "parse":
            from agent_core.rag.parsing import parse_document

            path = Path(request["source"])
            if path.stat().st_size != request["size"]:
                raise PreparationError(
                    "document_file_changed", "File đã lưu không còn khớp dữ liệu."
                )
            digest = hashlib.sha256()
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
            if digest.hexdigest() != request["sha256"]:
                raise PreparationError(
                    "document_file_changed", "File đã lưu không còn khớp dữ liệu."
                )
            parsed = parse_document(path, request["file_type"], work, options)
            write_json(work / "parsed.json", parsed.model_dump(), options["max_artifact_bytes"])
            result = {
                "ok": True,
                "parser_version": parsed.parser_version,
                "page_count": parsed.page_count,
                "warnings": parsed.warnings,
            }
        else:
            from agent_core.rag.chunking import prepare_chunks

            parsed_path = work / "parsed.json"
            if parsed_path.stat().st_size > options["max_artifact_bytes"]:
                raise PreparationError("document_artifact_limit", "Dữ liệu xử lý vượt giới hạn.")
            parsed = ParsedDocument.model_validate_json(parsed_path.read_bytes())
            prepared = prepare_chunks(parsed, request["title"], options)
            write_json(work / "chunks.json", prepared.model_dump(), options["max_artifact_bytes"])
            result = {"ok": True}
    except ModuleNotFoundError:
        result = {
            "ok": False,
            "code": "ingestion_dependencies_missing",
            "message": "Máy chủ chưa cài dependency xử lý tài liệu (extra ingestion).",
            "retryable": False,
        }
    except PreparationError as exc:
        result = {"ok": False, "code": exc.code, "message": str(exc), "retryable": exc.retryable}
    except MemoryError:
        result = {
            "ok": False,
            "code": "document_memory_limit",
            "message": "Không đủ bộ nhớ để xử lý tài liệu.",
            "retryable": False,
        }
    except Exception:
        # Vendor exceptions can contain filenames, text or paths; don't persist or log them.
        result = {
            "ok": False,
            "code": "document_processing_failed",
            "message": "Không xử lý được tài liệu. Kiểm tra định dạng và thử lại.",
            "retryable": False,
        }
    write_json(work / f"{args.phase}.result.json", result, options["max_artifact_bytes"])


if __name__ == "__main__":
    main()
