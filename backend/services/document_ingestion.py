"""Preparation and indexing on the existing queue with one fenced, resumable lease."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4, uuid5

import psutil
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import DBAPIError

from agent_core.rag.documents import ParsedDocument, PreparationError, PreparedDocument
from backend.core.config import PROJECT_ROOT, get_settings
from backend.database import SessionFactory
from backend.models import (
    BackgroundJob,
    Document,
    DocumentChunk,
    DocumentIngestionJob,
    DocumentVersion,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.background.worker import JobExecutionContext


def preparation_options() -> dict:
    settings = get_settings()
    return {
        "max_pages": settings.document_max_pages,
        "max_text_chars": settings.document_max_text_chars,
        "max_chunks": settings.document_max_chunks,
        "max_artifact_bytes": settings.document_max_artifact_bytes,
        "max_unpacked_bytes": settings.document_max_unpacked_bytes,
        "max_archive_entries": settings.document_max_archive_entries,
        "timeout_seconds": settings.document_process_timeout_seconds,
        "memory_mb": settings.document_process_memory_mb,
        "libreoffice_path": settings.document_libreoffice_path,
        "embedding_revision": settings.document_embedding_revision,
    }


async def enqueue_preparation(
    session: AsyncSession,
    document: Document,
    version: DocumentVersion,
    ingestion: DocumentIngestionJob | None = None,
) -> None:
    """Caller holds the owner/document locks; enqueue and upload/retry commit together."""
    from backend.services.document_indexing import has_prepared_content

    resume = has_prepared_content(version)
    job = BackgroundJob(
        id=uuid4(),
        job_type="documents.prepare",
        queue_name=get_settings().background_queue_name,
        user_id=document.owner_user_id,
        payload={"version_id": str(version.id)},
        max_attempts=3,
    )
    session.add(job)
    await session.flush()
    if ingestion is None:
        ingestion = DocumentIngestionJob(
            version_id=version.id,
            document_id=document.id,
            owner_user_id=document.owner_user_id,
        )
        session.add(ingestion)
    ingestion.background_job_id = job.id
    ingestion.status, ingestion.stage = "queued", "embedding" if resume else "parsing"
    ingestion.started_at = ingestion.finished_at = None
    ingestion.error_code = ingestion.error_message = None
    ingestion.completed_chunks, ingestion.total_chunks = 0, version.chunk_count if resume else None
    document.status, version.status = "queued", "queued"
    version.error_code = version.error_message = None
    version.pipeline_config = {
        **(version.pipeline_config if resume else {}),
        "preparation_options": preparation_options(),
        "awaiting_embedding": False,
    }


async def synchronize_terminal_preparations(session: AsyncSession, owner: UUID) -> None:
    """Expose queue terminal states even if a worker died before updating document progress."""
    from backend.services.documents import _lock_owner

    identifiers = (
        await session.scalars(
            select(DocumentIngestionJob.id)
            .join(BackgroundJob, BackgroundJob.id == DocumentIngestionJob.background_job_id)
            .where(
                DocumentIngestionJob.owner_user_id == owner,
                DocumentIngestionJob.status.in_(("queued", "running")),
                BackgroundJob.user_id == owner,
                BackgroundJob.status.in_(("failed", "cancelled")),
            )
        )
    ).all()
    if not identifiers:
        return
    await _lock_owner(session, owner)
    for identifier in identifiers:
        ingestion = await session.get(DocumentIngestionJob, identifier)
        if ingestion is None:
            continue
        document = await session.scalar(
            select(Document)
            .where(
                Document.id == ingestion.document_id,
                Document.owner_user_id == owner,
            )
            .with_for_update()
        )
        # Refresh after the owner lock: retry may have replaced the old background job.
        await session.refresh(ingestion)
        job = await session.get(BackgroundJob, ingestion.background_job_id)
        if job is None or job.user_id != owner or job.status not in {"failed", "cancelled"}:
            continue
        if ingestion.status not in {"queued", "running"}:
            continue
        state = job.status
        version = await session.get(DocumentVersion, ingestion.version_id)
        if document is None or version is None:
            continue
        ingestion.status, ingestion.finished_at = state, job.finished_at or datetime.now(UTC)
        ingestion.error_code = job.error_code or f"document_{state}"
        ingestion.error_message = (
            "Đã dừng xử lý tài liệu."
            if state == "cancelled"
            else ("Worker không hoàn tất xử lý tài liệu. Bạn có thể thử lại.")
        )
        version.status, version.error_code = state, ingestion.error_code
        version.error_message = ingestion.error_message
        if document.deleted_at is None and document.active_version_id is None:
            document.status = state
    await session.commit()


async def retry_preparation(session: AsyncSession, owner: UUID, document_id: UUID):
    from backend.services.documents import (
        DocumentServiceError,
        _library_query,
        _lock_owner,
        get_document,
    )

    await synchronize_terminal_preparations(session, owner)
    await _lock_owner(session, owner)
    row = (
        await session.execute(
            _library_query(owner)
            .where(
                Document.id == document_id,
                Document.deleted_at.is_(None),
                Document.status != "deleting",
            )
            .with_for_update(of=Document)
        )
    ).first()
    if row is None:
        raise DocumentServiceError("Không tìm thấy tài liệu.")
    document, version = row
    ingestion = await session.scalar(
        select(DocumentIngestionJob).where(
            DocumentIngestionJob.version_id == version.id,
            DocumentIngestionJob.owner_user_id == owner,
        )
    )
    job = await session.get(BackgroundJob, ingestion.background_job_id) if ingestion else None
    if job and job.status in {"queued", "running"}:
        await session.rollback()
        return await get_document(session, owner, document_id)
    pending_embedding = version.status == "embedding" and version.pipeline_config.get(
        "awaiting_embedding"
    )
    if (
        version.status not in {"uploaded", "failed", "cancelled"} and not pending_embedding
    ) or document.active_version_id:
        raise DocumentServiceError(
            "Tài liệu đã xử lý hoặc đang chờ bước tiếp theo; không cần gửi xử lý lại.",
            status_code=409,
            code="document_retry_unavailable",
        )
    await enqueue_preparation(session, document, version, ingestion)
    await session.commit()
    return await get_document(session, owner, document_id)


async def cancel_preparation(session: AsyncSession, owner: UUID, document_id: UUID) -> dict:
    from backend.services.documents import DocumentServiceError, _lock_owner

    await _lock_owner(session, owner)
    document = await session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.owner_user_id == owner,
            Document.deleted_at.is_(None),
            Document.status != "deleting",
        )
        .with_for_update()
    )
    if document is None:
        raise DocumentServiceError("Không tìm thấy tài liệu.")
    row = (
        await session.execute(
            select(BackgroundJob, DocumentIngestionJob)
            .join(DocumentIngestionJob, DocumentIngestionJob.background_job_id == BackgroundJob.id)
            .where(
                DocumentIngestionJob.document_id == document_id,
                DocumentIngestionJob.owner_user_id == owner,
                BackgroundJob.user_id == owner,
                BackgroundJob.status.in_(("queued", "running")),
            )
            .with_for_update(of=BackgroundJob)
        )
    ).first()
    if row is None:
        raise DocumentServiceError(
            "Không có tác vụ xử lý đang chạy.",
            status_code=409,
            code="document_not_processing",
        )
    job, ingestion = row
    now = datetime.now(UTC)
    job.cancel_requested_at = now
    state = "cancel_requested"
    if job.status == "queued":
        job.status, job.cancelled_at, job.finished_at = "cancelled", now, now
        ingestion.status, ingestion.finished_at = "cancelled", now
        ingestion.error_code, ingestion.error_message = (
            "document_cancelled",
            "Đã dừng xử lý tài liệu.",
        )
        version = await session.get(DocumentVersion, ingestion.version_id)
        version.status = "cancelled"
        version.error_code, version.error_message = ingestion.error_code, ingestion.error_message
        document.status = "cancelled"
        state = "cancelled"
    await session.commit()
    return {"id": document_id, "status": state, "message": "Đã yêu cầu dừng xử lý tài liệu."}


async def _locked(
    session: AsyncSession, context: JobExecutionContext, version_id: UUID, *, stopping=False
):
    from backend.background.queue import BackgroundJobOwnershipError
    from backend.background.worker import JobCancellationRequested
    from backend.services.documents import _lock_owner

    owner = await session.scalar(
        select(BackgroundJob.user_id).where(BackgroundJob.id == context.job.id)
    )
    target = (
        await session.execute(
            select(DocumentVersion.document_id).where(
                DocumentVersion.id == version_id,
                DocumentVersion.owner_user_id == owner,
            )
        )
    ).first()
    if owner is None or target is None:
        raise BackgroundJobOwnershipError("Document preparation lost its owner or version")
    await _lock_owner(session, owner)
    document = await session.scalar(
        select(Document)
        .where(
            Document.id == target.document_id,
            Document.owner_user_id == owner,
        )
        .with_for_update()
    )
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.id == context.job.id,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if (
        job is None
        or job.user_id != owner
        or job.job_type != "documents.prepare"
        or job.payload.get("version_id") != str(version_id)
        or job.status != "running"
        or job.worker_id != context.job.worker_id
        or job.started_at != context.job.started_at
        or job.attempt_count != context.job.attempt_count
        or job.lease_expires_at is None
        or job.lease_expires_at <= now
    ):
        raise BackgroundJobOwnershipError("Document preparation lease is no longer owned")
    ingestion = await session.scalar(
        select(DocumentIngestionJob)
        .where(
            DocumentIngestionJob.version_id == version_id,
            DocumentIngestionJob.owner_user_id == owner,
            DocumentIngestionJob.background_job_id == job.id,
        )
        .with_for_update()
    )
    version = await session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.id == version_id,
            DocumentVersion.owner_user_id == owner,
        )
        .with_for_update()
    )
    if document is None or version is None or ingestion is None:
        raise BackgroundJobOwnershipError("Document preparation has been replaced")
    if not stopping and (job.cancel_requested_at is not None or document.deleted_at is not None):
        raise JobCancellationRequested("Đã yêu cầu dừng xử lý tài liệu.")
    return document, version, ingestion, job


def _terminate_tree(pid: int) -> None:
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for process in [*reversed(children), parent]:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs([*children, parent], timeout=5)
    except psutil.NoSuchProcess:
        pass


async def _run_phase(context: JobExecutionContext, work: Path, phase: str, options: dict) -> dict:
    cache = get_settings().document_model_cache_path
    if not cache.is_absolute():
        cache = PROJECT_ROOT / cache
    cache.mkdir(parents=True, exist_ok=True)
    scratch = work / "scratch"
    scratch.mkdir(exist_ok=True, mode=0o700)
    # Don't pass DB/SMTP/provider credentials into the parser process.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "PATHEXT",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        }
    }
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HF_HOME": str(cache / "huggingface"),
            "DOCLING_CACHE_DIR": str(cache / "docling"),
            "NLTK_DATA": str(cache / "nltk"),
            "TOKENIZERS_PARALLELISM": "false",
            "OMP_NUM_THREADS": "2",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "TEMP": str(scratch),
            "TMP": str(scratch),
            "TMPDIR": str(scratch),
        }
    )
    kwargs = (
        {"creationflags": subprocess.CREATE_NO_WINDOW}
        if os.name == "nt"
        else {
            "start_new_session": True,
        }
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "scripts.process_document",
        "--phase",
        phase,
        "--request",
        str(work / "request.json"),
        cwd=PROJECT_ROOT,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **kwargs,
    )
    deadline = asyncio.get_running_loop().time() + options["timeout_seconds"]
    try:
        while process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=0.5)
                break
            except TimeoutError:
                pass
            await context.raise_if_cancelled()
            if asyncio.get_running_loop().time() >= deadline:
                raise PreparationError(
                    "document_timeout", "Xử lý tài liệu vượt giới hạn thời gian."
                )
            try:
                monitor = psutil.Process(process.pid)
                usage = sum(
                    item.memory_info().rss
                    for item in [monitor, *monitor.children(recursive=True)]
                    if item.is_running()
                )
                if usage > options["memory_mb"] * 1024 * 1024:
                    raise PreparationError(
                        "document_memory_limit", "Xử lý tài liệu vượt giới hạn bộ nhớ."
                    )
            except psutil.NoSuchProcess:
                pass
        path = work / f"{phase}.result.json"
        if process.returncode or not path.is_file():
            raise PreparationError(
                "document_process_failed", "Tiến trình xử lý tài liệu không hoàn tất."
            )
        if path.stat().st_size > options["max_artifact_bytes"]:
            raise PreparationError("document_artifact_limit", "Dữ liệu xử lý vượt giới hạn.")
        result = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        if not result.get("ok"):
            raise PreparationError(
                result["code"], result["message"], retryable=result.get("retryable", False)
            )
        return result
    finally:
        await asyncio.to_thread(_terminate_tree, process.pid)
        await process.wait()


def _load_artifacts(work: Path, options: dict):
    for name in ("parsed.json", "chunks.json"):
        if (work / name).stat().st_size > options["max_artifact_bytes"]:
            raise PreparationError("document_artifact_limit", "Dữ liệu xử lý vượt giới hạn.")
    parsed = ParsedDocument.model_validate_json((work / "parsed.json").read_bytes())
    prepared = PreparedDocument.model_validate_json((work / "chunks.json").read_bytes())
    if not prepared.chunks or len(prepared.chunks) > options["max_chunks"]:
        raise PreparationError("document_chunk_limit", "Số chunk không hợp lệ.")
    for chunk in prepared.chunks:
        if (
            parsed.original_text[chunk.start_offset : chunk.end_offset] != chunk.text
            or hashlib.sha256(chunk.text.encode("utf-8")).hexdigest() != chunk.content_sha256
        ):
            raise PreparationError(
                "chunk_source_mapping", "Tọa độ chunk không khớp bản trích xuất."
            )
    return parsed, prepared


async def _record_interruption(context, version_id, state, code, message):
    from backend.background.queue import BackgroundJobOwnershipError

    try:
        async with SessionFactory() as session:
            document, version, ingestion, job = await _locked(
                session, context, version_id, stopping=True
            )
            if ingestion.status == "succeeded":
                return  # A COMMIT may have succeeded even when its response was lost.
            if job.cancel_requested_at is not None or document.deleted_at is not None:
                state, code, message = "cancelled", "document_cancelled", "Đã dừng xử lý tài liệu."
            ingestion.status = state
            ingestion.error_code, ingestion.error_message = code, message
            ingestion.finished_at = None if state == "queued" else datetime.now(UTC)
            version.status, version.error_code, version.error_message = state, code, message
            if document.deleted_at is None and document.active_version_id is None:
                document.status = state
            await session.commit()
    except (BackgroundJobOwnershipError, DBAPIError):
        pass  # A fresh worker or the next status read reconciles terminal queue states.


async def prepare_document(context: JobExecutionContext, version_id: UUID) -> dict:
    from backend.background.queue import BackgroundJobOwnershipError
    from backend.background.worker import JobCancellationRequested, RetryableJobError
    from backend.services.document_indexing import index_if_prepared
    from backend.services.document_storage import parsed_key
    from backend.services.documents import document_storage

    work = None
    storage = document_storage()
    staging_root = (storage.root / ".ingestion").resolve()
    try:
        await context.raise_if_cancelled()
        indexed = await index_if_prepared(context, version_id)
        if indexed is not None:
            return indexed
        async with SessionFactory() as session:
            document, version, ingestion, _job = await _locked(session, context, version_id)
            options = version.pipeline_config.get("preparation_options") or preparation_options()
            source = storage.download_path(version.storage_key)
            request = {
                "source": str(source),
                "title": document.title,
                "file_type": version.file_type,
                "size": version.file_size_bytes,
                "sha256": version.file_sha256,
                "options": options,
                "parent_pid": os.getpid(),
                "parent_created_at": psutil.Process().create_time(),
            }
            owner, document_id = document.owner_user_id, document.id
            staging_root = storage.staging_directory(owner, document_id, version_id)
            ingestion.status, ingestion.stage = "running", "parsing"
            ingestion.started_at, ingestion.finished_at = datetime.now(UTC), None
            ingestion.error_code = ingestion.error_message = None
            document.status, version.status = "parsing", "parsing"
            version.error_code = version.error_message = None
            await session.commit()
        if not staging_root.is_relative_to(storage.root):
            raise PreparationError(
                "document_storage_invalid", "Không tạo được vùng xử lý tài liệu."
            )
        staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        work = Path(tempfile.mkdtemp(prefix=f"{context.job.id}-", dir=staging_root)).resolve()
        (work / "request.json").write_text(
            json.dumps(request, ensure_ascii=False), encoding="utf-8"
        )
        os.chmod(work / "request.json", 0o600)
        info = await _run_phase(context, work, "parse", options)
        async with SessionFactory() as session:
            document, version, ingestion, _job = await _locked(session, context, version_id)
            document.status, version.status, ingestion.stage = "chunking", "chunking", "chunking"
            version.parser_version, version.page_count = info["parser_version"], info["page_count"]
            version.warnings = info["warnings"]
            await session.commit()
        await _run_phase(context, work, "chunk", options)
        parsed, prepared = await asyncio.to_thread(_load_artifacts, work, options)
        await context.raise_if_cancelled()
        async with SessionFactory() as session:
            document, version, ingestion, _job = await _locked(session, context, version_id)
            config = {
                "owner": str(owner),
                "file_sha256": version.file_sha256,
                "parser": parsed.parser_version,
                "chunker": prepared.chunker_version,
                "embedding_model": version.embedding_model,
                "embedding_revision": prepared.tokenizer_revision,
                "chunk_size": 512,
                "chunk_overlap": 64,
                "normalization": "nfc-whitespace-v1",
            }
            pipeline_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
            await session.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.version_id == version_id,
                    DocumentChunk.owner_user_id == owner,
                )
            )
            for begin in range(0, len(prepared.chunks), 500):
                rows = []
                for index, chunk in enumerate(prepared.chunks[begin : begin + 500], begin):
                    rows.append(
                        {
                            **chunk.model_dump(),
                            "id": uuid5(
                                version_id, f"{pipeline_hash}:{index}:{chunk.content_sha256}"
                            ),
                            "owner_user_id": owner,
                            "document_id": document_id,
                            "version_id": version_id,
                            "chunk_index": index,
                        }
                    )
                await session.execute(insert(DocumentChunk), rows)
            # Publish only a complete artifact; cancellation/DB failure cannot publish ready data.
            key = parsed_key(owner, document_id, version_id)
            await storage.publish_parsed(work / "parsed.json", key)
            version.parsed_storage_key = key
            version.pipeline_hash = pipeline_hash
            version.parser_version, version.chunker_version = (
                parsed.parser_version,
                prepared.chunker_version,
            )
            version.embedding_revision = prepared.tokenizer_revision
            version.chunk_size, version.chunk_overlap = 512, 64
            version.page_count, version.chunk_count = parsed.page_count, len(prepared.chunks)
            version.warnings = parsed.warnings + prepared.warnings
            version.pipeline_config = {
                **version.pipeline_config,
                **config,
                "awaiting_embedding": False,
                "content_prepared": True,
            }
            version.status, document.status = "embedding", "embedding"
            ingestion.status, ingestion.stage, ingestion.finished_at = (
                "running",
                "embedding",
                None,
            )
            ingestion.completed_chunks, ingestion.total_chunks = 0, len(prepared.chunks)
            await session.commit()
        indexed = await index_if_prepared(context, version_id)
        if indexed is None:
            raise PreparationError("document_chunks_incomplete", "Nội dung chưa đủ để lập index.")
        return indexed
    except (asyncio.CancelledError, JobCancellationRequested):
        cancelled = await context.queue.is_cancel_requested(context.job)
        state = "cancelled" if cancelled else "queued"
        await _record_interruption(
            context, version_id, state, "document_interrupted", "Xử lý tài liệu đã dừng."
        )
        raise
    except BackgroundJobOwnershipError:
        raise
    except (PreparationError, OSError, DBAPIError) as exc:
        code = exc.code if isinstance(exc, PreparationError) else "document_processing_io"
        message = (
            str(exc)
            if isinstance(exc, PreparationError)
            else "Chưa thể đọc/ghi dữ liệu xử lý tài liệu."
        )
        retryable = exc.retryable if isinstance(exc, PreparationError) else True
        state = (
            "queued"
            if retryable and context.job.attempt_count < context.job.max_attempts
            else "failed"
        )
        await _record_interruption(context, version_id, state, code, message)
        if retryable:
            raise RetryableJobError(message, error_code=code) from None
        raise PreparationError(code, message) from None
    except Exception:
        await _record_interruption(
            context,
            version_id,
            "failed",
            "document_processing_failed",
            "Không hoàn tất được quá trình xử lý tài liệu.",
        )
        raise PreparationError(
            "document_processing_failed", "Không hoàn tất được quá trình xử lý tài liệu."
        ) from None
    finally:
        if work is not None:
            # Delete only this attempt's directory within the private staging root.
            resolved = work.resolve()
            if resolved != staging_root and resolved.is_relative_to(staging_root):
                await asyncio.to_thread(shutil.rmtree, resolved, ignore_errors=True)
