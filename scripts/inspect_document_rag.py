"""Owner-scoped, read-only local M3 inspection; does not call LLMs or inference models."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def inspect_document(conn, owner, args):
    from backend.services.documents import document_storage

    document = conn.execute(
        "SELECT id, title, status, active_version_id, deleted_at FROM documents "
        "WHERE id=%s AND owner_user_id=%s",
        (args.document_id, owner),
    ).fetchone()
    if not document:
        raise ValueError("Document not found for this owner.")
    versions = conn.execute(
        "SELECT id, version_number, status, chunk_count, page_count, parser_version, "
        "chunker_version, embedding_model, embedding_revision, embedding_dimensions, "
        "pipeline_hash, pipeline_config, warnings, error_code, error_message, parsed_storage_key "
        "FROM document_versions WHERE document_id=%s AND owner_user_id=%s "
        "ORDER BY version_number DESC",
        (args.document_id, owner),
    ).fetchall()
    report = {
        "document": document,
        "versions": [
            {key: value for key, value in version.items() if key != "parsed_storage_key"}
            for version in versions
        ],
    }
    if document["deleted_at"] is not None:
        report["notice"] = "Document deleted; content inspection is disabled."
        return report
    selected = (
        next((v for v in versions if v["id"] == args.version_id), None)
        if args.version_id
        else next(iter(versions), None)
    )
    if selected is None:
        raise ValueError("Document version not found for this owner/document.")
    scope = (args.document_id, selected["id"], owner)
    report["selected_version_id"] = selected["id"]
    report["ingestion"] = conn.execute(
        "SELECT status, stage, completed_chunks, total_chunks, error_code, error_message "
        "FROM document_ingestion_jobs WHERE document_id=%s AND version_id=%s AND owner_user_id=%s",
        scope,
    ).fetchall()
    report["index"] = conn.execute(
        "SELECT count(*) AS chunks, count(embedding) AS embedded_chunks, "
        "count(*) FILTER (WHERE search_tsv <> ''::tsvector) AS lexical_chunks, "
        "count(*) FILTER (WHERE token_count > 512 OR token_count <= 0) AS invalid_token_counts, "
        "min(token_count) AS min_tokens, max(token_count) AS max_tokens, "
        "min(vector_dims(embedding)) AS min_dimensions, "
        "max(vector_dims(embedding)) AS max_dimensions, "
        "min(vector_norm(embedding)) AS min_vector_norm, "
        "max(vector_norm(embedding)) AS max_vector_norm "
        "FROM document_chunks WHERE document_id=%s AND version_id=%s AND owner_user_id=%s",
        scope,
    ).fetchone()
    parsed = None
    if selected["parsed_storage_key"]:
        from agent_core.rag.documents import ParsedDocument

        artifact = document_storage().download_path(selected["parsed_storage_key"])
        parsed = ParsedDocument.model_validate_json(artifact.read_bytes())
        report["parsed"] = {
            "characters": len(parsed.original_text),
            "blocks": len(parsed.blocks),
            "page_count": parsed.page_count,
            "warnings": parsed.warnings,
            "text": parsed.original_text if args.full_text else parsed.original_text[:4000],
        }
    tokenizer = None
    if args.verify_tokens:
        if not selected["embedding_revision"]:
            raise ValueError("No tokenizer revision yet; wait for the chunking stage.")
        from backend.core.config import get_settings

        os.environ["HF_HOME"] = str(get_settings().document_model_cache_path / "huggingface")
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(
            selected["embedding_model"],
            revision=selected["embedding_revision"],
            local_files_only=True,
            trust_remote_code=False,
        )
    checks = {
        "checked_chunks": 0,
        "offset_mismatches": 0,
        "hash_mismatches": 0,
        "token_mismatches": 0 if tokenizer else None,
        "source_available": parsed is not None,
    }
    report["chunk_samples"] = []
    with conn.cursor(name="rag_chunk_inspection") as cursor:
        cursor.execute(
            "SELECT id, chunk_index, text, embedding_text, lexical_text, token_count, "
            "content_sha256, page_start, page_end, section, start_offset, end_offset, "
            "metadata_json "
            "FROM document_chunks WHERE document_id=%s AND version_id=%s AND owner_user_id=%s "
            "ORDER BY chunk_index",
            scope,
        )
        for chunk in cursor:
            checks["checked_chunks"] += 1
            if parsed:
                start, end = chunk["start_offset"], chunk["end_offset"]
                if (
                    start is None
                    or end is None
                    or not 0 <= start < end <= len(parsed.original_text)
                    or parsed.original_text[start:end] != chunk["text"]
                ):
                    checks["offset_mismatches"] += 1
            if hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest() != chunk["content_sha256"]:
                checks["hash_mismatches"] += 1
            if tokenizer:
                actual = len(tokenizer.encode(chunk["embedding_text"], add_special_tokens=True))
                if actual != chunk["token_count"] or actual > 512:
                    checks["token_mismatches"] += 1
            if len(report["chunk_samples"]) < args.chunks:
                if not args.full_text:
                    for key in ("text", "embedding_text", "lexical_text"):
                        chunk[key] = chunk[key][:1200]
                report["chunk_samples"].append(chunk)
    report["source_checks"] = checks
    return report


def inspect_runs(conn, owner, args):
    if args.run_id:
        row = conn.execute(
            "SELECT r.id, r.conversation_id, r.status, r.stop_reason, r.error_message, r.rag_trace "
            "FROM agent_runs r JOIN conversations c ON c.id=r.conversation_id "
            "WHERE r.id=%s AND c.user_id=%s",
            (args.run_id, owner),
        ).fetchone()
        if row is None:
            raise ValueError("Run not found for this owner.")
        return row
    return conn.execute(
        "SELECT r.id, r.conversation_id, r.status, r.created_at, "
        "r.rag_trace IS NOT NULL AS has_rag_trace, left(m.content,200) AS question "
        "FROM agent_runs r JOIN conversations c ON c.id=r.conversation_id "
        "LEFT JOIN messages m ON m.id=r.user_message_id AND m.conversation_id=c.id "
        "WHERE c.user_id=%s ORDER BY r.created_at DESC LIMIT %s",
        (owner, args.limit),
    ).fetchall()


def bounded_int(value):
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("Choose a number from 1 to 100.")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Explicit owner of the test documents.")
    parser.add_argument("--output", type=Path, help="Optional JSON report; stdout otherwise.")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="List this owner's document IDs.")
    listing.add_argument("--limit", type=bounded_int, default=30)
    document = commands.add_parser("document", help="Inspect parsed text, chunks and index state.")
    document.add_argument("--document-id", required=True, type=UUID)
    document.add_argument(
        "--version-id", type=UUID, help="Default: latest version, including reindex."
    )
    document.add_argument("--chunks", type=bounded_int, default=6)
    document.add_argument("--full-text", action="store_true")
    document.add_argument("--verify-tokens", action="store_true", help="Use cached tokenizer only.")
    runs = commands.add_parser("runs", help="List runs or inspect one RAG trace.")
    runs.add_argument("--run-id", type=UUID)
    runs.add_argument("--limit", type=bounded_int, default=10)
    args = parser.parse_args()
    from backend.core.config import get_settings

    try:
        settings = get_settings()
        with psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_name,
            user=settings.db_user,
            password=settings.db_password.get_secret_value(),
            row_factory=dict_row,
            connect_timeout=10,
        ) as conn:
            conn.read_only = True
            conn.execute("SET LOCAL statement_timeout = '30s'")
            owner = conn.execute(
                "SELECT id FROM users WHERE lower(email)=lower(%s)",
                (args.email,),
            ).fetchone()
            if owner is None:
                raise ValueError("Test user not found; create it through the web first.")
            if args.command == "document":
                report = inspect_document(conn, owner["id"], args)
            elif args.command == "runs":
                report = inspect_runs(conn, owner["id"], args)
            else:
                report = conn.execute(
                    "SELECT id, title, status, active_version_id, deleted_at FROM documents "
                    "WHERE owner_user_id=%s ORDER BY created_at DESC LIMIT %s",
                    (owner["id"], args.limit),
                ).fetchall()
        output = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        if args.output:
            # Never replace a previous report silently.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as file:
                file.write(output + "\n")
            print(f"Report saved: {args.output}")
        else:
            print(output)
    except ValidationError:
        parser.exit(
            1, "Invalid configuration or parsed artifact; check settings/document status.\n"
        )
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    except (psycopg.Error, OSError) as exc:
        parser.exit(1, f"Inspection failed ({type(exc).__name__}); no changes were committed.\n")


if __name__ == "__main__":
    main()
