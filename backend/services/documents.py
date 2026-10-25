"""Owner-scoped library operations with durable preparation and deferred RAG publication."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from starlette.datastructures import UploadFile

from backend.core.config import Settings, get_settings
from backend.database import SessionFactory
from backend.models import (
    AgentRun,
    BackgroundJob,
    Conversation,
    ConversationDocument,
    Document,
    DocumentIngestionJob,
    DocumentVersion,
    User,
)
from backend.schemas.document import (
    ConversationDocumentsResponse,
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUsageResponse,
    IngestionProgressResponse,
)
from backend.services.document_storage import (
    MIME_TYPES,
    LocalDocumentStorage,
    original_key,
    parsed_key,
    validate_filename,
)

if TYPE_CHECKING:
    from backend.background.worker import JobExecutionContext


class DocumentServiceError(RuntimeError):
    def __init__(self, detail: str, *, status_code=404, code="document_not_found"):
        self.detail, self.status_code, self.code = detail, status_code, code
        super().__init__(detail)


def document_storage(settings: Settings | None = None) -> LocalDocumentStorage:
    config = settings or get_settings()
    root = config.document_storage_path
    if not root.is_absolute():
        from backend.core.config import PROJECT_ROOT

        root = PROJECT_ROOT / root
    return LocalDocumentStorage(
        root,
        max_unpacked_bytes=config.document_max_unpacked_bytes,
        max_entries=config.document_max_archive_entries,
    )


def _view(document: Document, version: DocumentVersion, *, deduplicated=False) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        title=document.title,
        status="deleting" if document.deleted_at is not None else version.status,
        revision=document.revision,
        active_version_id=document.active_version_id,
        version_id=version.id,
        version_number=version.version_number,
        original_filename=version.original_filename,
        file_type=version.file_type,
        mime_type=version.mime_type,
        file_size_bytes=version.file_size_bytes,
        file_sha256=version.file_sha256,
        page_count=version.page_count,
        chunk_count=version.chunk_count,
        warnings=version.warnings,
        error_code=version.error_code,
        error_message=version.error_message,
        ready_for_qa=(document.status == "ready" and document.active_version_id is not None),
        reindexing=document.active_version_id is not None
        and document.active_version_id != version.id,
        awaiting_embedding=(
            document.status == "embedding"
            and version.status == "embedding"
            and bool(version.pipeline_config.get("awaiting_embedding", False))
        ),
        deduplicated=deduplicated,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _library_query(owner: UUID):
    latest_id = (
        select(DocumentVersion.id)
        .where(DocumentVersion.document_id == Document.id, DocumentVersion.owner_user_id == owner)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
        .correlate(Document)
        .scalar_subquery()
    )
    return (
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.id == latest_id)
        .where(Document.owner_user_id == owner, DocumentVersion.owner_user_id == owner)
    )


def _ready_for_qa(owner: UUID):
    active = aliased(DocumentVersion)
    return (
        (Document.status == "ready")
        & Document.deleted_at.is_(None)
        & select(active.id)
        .where(
            active.id == Document.active_version_id,
            active.document_id == Document.id,
            active.owner_user_id == owner,
            active.status == "ready",
        )
        .correlate(Document)
        .exists()
    )


async def _lock_owner(session: AsyncSession, owner: UUID) -> None:
    # All storage-quota mutations share this lock, including cleanup, across app processes.
    found = await session.scalar(select(User.id).where(User.id == owner).with_for_update())
    if found is None:
        raise DocumentServiceError("Không tìm thấy tài khoản.")


async def document_usage(session: AsyncSession, owner: UUID) -> DocumentUsageResponse:
    settings = get_settings()
    stored_files = (
        select(func.max(DocumentVersion.file_size_bytes).label("size"))
        .where(DocumentVersion.owner_user_id == owner)
        .group_by(DocumentVersion.storage_key)
        .subquery()
    )
    used = await session.scalar(select(func.coalesce(func.sum(stored_files.c.size), 0)))
    count = await session.scalar(
        select(func.count()).select_from(Document).where(Document.owner_user_id == owner)
    )
    # Deleting data still consumes quota until the worker has removed the stored files.
    return DocumentUsageResponse(
        used_bytes=used,
        document_count=count,
        max_file_bytes=settings.document_max_file_bytes,
        max_user_bytes=settings.document_max_user_bytes,
        max_documents=settings.document_max_documents,
    )


async def list_documents(
    session: AsyncSession,
    owner: UUID,
    *,
    query: str | None,
    file_type: str | None,
    status: str | None,
    include_deleting: bool,
    limit: int,
    offset: int,
    ready_for_qa: bool | None = None,
) -> DocumentListResponse:
    from backend.services.document_ingestion import synchronize_terminal_preparations

    await synchronize_terminal_preparations(session, owner)
    filters = [Document.owner_user_id == owner]
    if not include_deleting and status != "deleting":
        filters.append(Document.deleted_at.is_(None))
    if query:
        filters.append(Document.title.icontains(query.strip(), autoescape=True))
    statement = _library_query(owner).where(*filters)
    if ready_for_qa is not None:
        readiness = _ready_for_qa(owner)
        statement = statement.where(readiness if ready_for_qa else ~readiness)
    if status == "deleting":
        statement = statement.where(Document.status == "deleting")
    elif status:
        statement = statement.where(DocumentVersion.status == status, Document.deleted_at.is_(None))
    if file_type:
        statement = statement.where(DocumentVersion.file_type == file_type)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = (
        await session.execute(
            statement.order_by(Document.updated_at.desc(), Document.id).offset(offset).limit(limit)
        )
    ).all()
    deleting_ids = [str(document.id) for document, _ in rows if document.deleted_at is not None]
    cleanup_statuses = {}
    if deleting_ids:
        target = BackgroundJob.payload["document_id"].astext
        cleanup_rows = (
            await session.execute(
                select(target.label("document_id"), BackgroundJob.status)
                .where(
                    BackgroundJob.user_id == owner,
                    BackgroundJob.job_type == "documents.cleanup",
                    target.in_(deleting_ids),
                )
                .distinct(target)
                .order_by(target, BackgroundJob.created_at.desc(), BackgroundJob.id.desc())
            )
        ).all()
        cleanup_statuses = {row.document_id: row.status for row in cleanup_rows}
    return DocumentListResponse(
        items=[
            _view(document, version).model_copy(
                update={
                    "cleanup_status": cleanup_statuses.get(str(document.id)),
                }
            )
            for document, version in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_document(
    session: AsyncSession, owner: UUID, document_id: UUID
) -> DocumentDetailResponse:
    from backend.services.document_ingestion import synchronize_terminal_preparations

    await synchronize_terminal_preparations(session, owner)
    row = (
        await session.execute(
            _library_query(owner).where(
                Document.id == document_id,
                Document.deleted_at.is_(None),
                Document.status != "deleting",
            )
        )
    ).first()
    if row is None:
        raise DocumentServiceError("Không tìm thấy tài liệu.")
    document, version = row
    ingestion = await session.scalar(
        select(DocumentIngestionJob).where(
            DocumentIngestionJob.owner_user_id == owner,
            DocumentIngestionJob.version_id == version.id,
        )
    )
    progress = (
        None
        if ingestion is None
        else IngestionProgressResponse(
            status=ingestion.status,
            stage=ingestion.stage,
            completed_chunks=ingestion.completed_chunks,
            total_chunks=ingestion.total_chunks,
            error_code=ingestion.error_code,
            error_message=ingestion.error_message,
        )
    )
    return DocumentDetailResponse(**_view(document, version).model_dump(), ingestion=progress)


async def _lock_conversation(session: AsyncSession, owner: UUID, conversation_id: UUID) -> None:
    conversation = await session.scalar(
        select(Conversation.id)
        .where(Conversation.id == conversation_id, Conversation.user_id == owner)
        .with_for_update()
    )
    if conversation is None:
        raise DocumentServiceError("Không tìm thấy hội thoại.", code="conversation_not_found")
    active = await session.scalar(
        select(AgentRun.id)
        .where(
            AgentRun.conversation_id == conversation_id,
            AgentRun.status.in_(("queued", "running", "waiting_approval")),
        )
        .limit(1)
    )
    if active is not None:
        raise DocumentServiceError(
            "Hội thoại đang xử lý. Hãy đợi hoàn tất rồi thay đổi tài liệu.",
            status_code=409,
            code="conversation_busy",
        )


async def get_conversation_documents(
    session: AsyncSession, owner: UUID, conversation_id: UUID
) -> ConversationDocumentsResponse:
    from backend.services.document_ingestion import synchronize_terminal_preparations

    await synchronize_terminal_preparations(session, owner)
    conversation = await session.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id, Conversation.user_id == owner
        )
    )
    if conversation is None:
        raise DocumentServiceError("Không tìm thấy hội thoại.", code="conversation_not_found")
    rows = (
        await session.execute(
            _library_query(owner)
            .join(ConversationDocument, ConversationDocument.document_id == Document.id)
            .where(
                ConversationDocument.conversation_id == conversation_id,
                ConversationDocument.owner_user_id == owner,
                Document.deleted_at.is_(None),
                Document.status != "deleting",
            )
            .order_by(ConversationDocument.created_at, Document.id)
        )
    ).all()
    return ConversationDocumentsResponse(
        conversation_id=conversation_id,
        items=[_view(document, version) for document, version in rows],
    )


async def set_conversation_documents(
    session: AsyncSession, owner: UUID, conversation_id: UUID, document_ids: list[UUID]
) -> ConversationDocumentsResponse:
    await _lock_owner(session, owner)
    await _lock_conversation(session, owner, conversation_id)
    if document_ids:
        available = (
            await session.execute(
                select(Document.id, _ready_for_qa(owner).label("ready_for_qa")).where(
                    Document.id.in_(document_ids),
                    Document.owner_user_id == owner,
                    Document.deleted_at.is_(None),
                    Document.status != "deleting",
                )
            )
        ).all()
        if {row.id for row in available} != set(document_ids):
            raise DocumentServiceError("Một số tài liệu không còn khả dụng.")
        if any(not row.ready_for_qa for row in available):
            raise DocumentServiceError(
                "Chỉ có thể chọn tài liệu đã sẵn sàng hỏi đáp. Hãy làm mới thư viện.",
                status_code=409,
                code="documents_not_ready",
            )
    await session.execute(
        delete(ConversationDocument).where(
            ConversationDocument.conversation_id == conversation_id,
            ConversationDocument.owner_user_id == owner,
        )
    )
    session.add_all(
        [
            ConversationDocument(
                conversation_id=conversation_id, document_id=identifier, owner_user_id=owner
            )
            for identifier in document_ids
        ]
    )
    await session.commit()
    return await get_conversation_documents(session, owner, conversation_id)


async def upload_document(
    session: AsyncSession, owner: UUID, upload: UploadFile
) -> DocumentResponse:
    from backend.services.document_ingestion import enqueue_preparation

    settings, storage = get_settings(), document_storage()
    name, file_type = validate_filename(upload.filename, upload.content_type)
    if upload.size is not None and upload.size > settings.document_max_file_bytes:
        raise DocumentServiceError(
            "File vượt giới hạn dung lượng.", status_code=413, code="document_too_large"
        )
    document_id, version_id = uuid4(), uuid4()
    key = original_key(owner, document_id, version_id, file_type)
    try:
        stored = await storage.save(upload.file, key, settings.document_max_file_bytes)
    except OSError as exc:
        raise DocumentServiceError(
            "Không thể lưu tài liệu.", status_code=503, code="document_storage_unavailable"
        ) from exc
    keep_file = False
    commit_started = False
    try:
        await _lock_owner(session, owner)
        duplicate = (
            await session.execute(
                _library_query(owner)
                .where(
                    Document.deleted_at.is_(None),
                    Document.status != "deleting",
                    DocumentVersion.file_sha256 == stored.sha256,
                    DocumentVersion.file_type == file_type,
                )
                .order_by(Document.created_at, Document.id)
                .limit(1)
            )
        ).first()
        if duplicate:
            document, version = duplicate
            if version.status == "uploaded":
                ingestion = await session.scalar(
                    select(DocumentIngestionJob).where(
                        DocumentIngestionJob.version_id == version.id,
                        DocumentIngestionJob.owner_user_id == owner,
                    )
                )
                await enqueue_preparation(session, document, version, ingestion)
            await session.commit()
            return _view(document, version, deduplicated=True)
        usage = await document_usage(session, owner)
        if usage.used_bytes + stored.size > usage.max_user_bytes:
            raise DocumentServiceError(
                "Thư viện đã vượt giới hạn dung lượng.",
                status_code=413,
                code="document_quota_exceeded",
            )
        if usage.document_count >= usage.max_documents:
            raise DocumentServiceError(
                "Thư viện đã đạt giới hạn số tài liệu.",
                status_code=409,
                code="document_count_exceeded",
            )
        document = Document(id=document_id, owner_user_id=owner, title=name, status="uploaded")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            owner_user_id=owner,
            version_number=1,
            original_filename=name,
            file_type=file_type,
            mime_type=MIME_TYPES[file_type],
            file_size_bytes=stored.size,
            file_sha256=stored.sha256,
            storage_key=stored.key,
            status="uploaded",
            warnings=stored.warnings,
        )
        session.add(version)
        await session.flush()
        await enqueue_preparation(session, document, version)
        commit_started = True
        try:
            await session.commit()
        except IntegrityError:
            # PostgreSQL rejected the transaction, so the file is definitely unreferenced.
            commit_started = False
            raise
        keep_file = True
        return _view(document, version)
    except BaseException:
        await session.rollback()
        raise
    finally:
        # On an ambiguous connection failure during COMMIT, preserve the file rather than
        # risk deleting a committed document. Reconciliation can clean orphaned files later.
        if not keep_file and not commit_started:
            await storage.delete(key)


async def download_document(
    session: AsyncSession, owner: UUID, document_id: UUID, version_id: UUID | None
) -> tuple[Path, str, str]:
    statement = (
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.id == document_id,
            Document.owner_user_id == owner,
            Document.deleted_at.is_(None),
            Document.status != "deleting",
            DocumentVersion.owner_user_id == owner,
        )
    )
    if version_id is not None:
        statement = statement.where(DocumentVersion.id == version_id)
    version = await session.scalar(
        statement.order_by(DocumentVersion.version_number.desc()).limit(1)
    )
    if version is None:
        raise DocumentServiceError("Không tìm thấy phiên bản tài liệu.")
    path = await asyncio.to_thread(document_storage().download_path, version.storage_key)
    return path, version.original_filename, version.mime_type


async def delete_document(
    session: AsyncSession, owner: UUID, document_id: UUID
) -> DocumentDeleteResponse:
    await _lock_owner(session, owner)
    document = await session.scalar(
        select(Document)
        .where(Document.id == document_id, Document.owner_user_id == owner)
        .with_for_update()
    )
    if document is None:
        raise DocumentServiceError("Không tìm thấy tài liệu.")
    now = datetime.now(UTC)
    if document.deleted_at is None:
        document.status, document.deleted_at, document.active_version_id = "deleting", now, None
        document.revision += 1
    jobs = (
        await session.scalars(
            select(BackgroundJob)
            .join(DocumentIngestionJob, DocumentIngestionJob.background_job_id == BackgroundJob.id)
            .where(
                DocumentIngestionJob.document_id == document_id,
                DocumentIngestionJob.owner_user_id == owner,
                BackgroundJob.user_id == owner,
                BackgroundJob.status.in_(("queued", "running")),
            )
            .with_for_update(of=BackgroundJob)
        )
    ).all()
    for job in jobs:
        job.cancel_requested_at = now
        if job.status == "queued":
            job.status, job.cancelled_at, job.finished_at = "cancelled", now, now
    existing = await session.scalar(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.user_id == owner,
            BackgroundJob.job_type == "documents.cleanup",
            BackgroundJob.payload["document_id"].astext == str(document_id),
            BackgroundJob.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    if existing is None:
        session.add(
            BackgroundJob(
                job_type="documents.cleanup",
                queue_name=get_settings().background_queue_name,
                user_id=owner,
                payload={"document_id": str(document_id)},
                max_attempts=5,
            )
        )
    await session.execute(
        delete(ConversationDocument).where(
            ConversationDocument.document_id == document_id,
            ConversationDocument.owner_user_id == owner,
        )
    )
    await session.commit()
    return DocumentDeleteResponse(id=document_id)


async def cleanup_deleted_document(context: JobExecutionContext, document_id: UUID) -> dict:
    from backend.background.worker import RetryableJobError

    await context.raise_if_cancelled()
    async with SessionFactory() as session:
        job = await session.get(BackgroundJob, context.job.id)
        if job is None or job.user_id is None:
            raise ValueError("Document cleanup requires a job owner")
        owner = job.user_id
        await _verify_cleanup_claim(session, context, owner, document_id)
    # Wait without owner/document locks so cancellation handlers can finish. Recover expired
    # leases during the wait instead of exhausting five quick retries behind a dead worker.
    deadline = asyncio.get_running_loop().time() + min(
        get_settings().background_job_lease_seconds + 5, 60
    )
    while True:
        await context.raise_if_cancelled()
        await context.queue.recover_expired_leases(queue_name=context.job.queue_name)
        async with SessionFactory() as session:
            await _verify_cleanup_claim(session, context, owner, document_id)
            running = await session.scalar(
                select(BackgroundJob.id)
                .join(
                    DocumentIngestionJob,
                    DocumentIngestionJob.background_job_id == BackgroundJob.id,
                )
                .where(
                    DocumentIngestionJob.document_id == document_id,
                    DocumentIngestionJob.owner_user_id == owner,
                    BackgroundJob.user_id == owner,
                    BackgroundJob.status == "running",
                )
                .limit(1)
            )
        if running is None:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise RetryableJobError(
                "Đang chờ dừng xử lý tài liệu.", error_code="document_ingestion_busy"
            )
        await asyncio.sleep(1)
    async with SessionFactory() as session:
        await _lock_owner(session, owner)
        document = await session.scalar(
            select(Document)
            .where(Document.id == document_id, Document.owner_user_id == owner)
            .with_for_update()
        )
        if document is None:
            return {"deleted": True}
        if document.status != "deleting" or document.deleted_at is None:
            raise ValueError("Only deleted documents can be cleaned up")
        await _verify_cleanup_claim(session, context, owner, document_id)
        running = await session.scalar(
            select(BackgroundJob.id)
            .join(DocumentIngestionJob, DocumentIngestionJob.background_job_id == BackgroundJob.id)
            .where(
                DocumentIngestionJob.document_id == document_id,
                DocumentIngestionJob.owner_user_id == owner,
                BackgroundJob.user_id == owner,
                BackgroundJob.status == "running",
            )
            .limit(1)
        )
        if running is not None:
            raise RetryableJobError(
                "Đang chờ dừng xử lý tài liệu.", error_code="document_ingestion_busy"
            )
        versions = (
            await session.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.owner_user_id == owner,
                )
            )
        ).all()
        storage = document_storage()
        try:
            await storage.cleanup_staging(owner, document_id)
            removed = set()
            for version in versions:
                await _verify_cleanup_claim(session, context, owner, document_id)
                if version.storage_key not in removed:
                    await storage.delete(version.storage_key)
                    removed.add(version.storage_key)
                # A file publish may precede an ambiguous/rolled-back metadata COMMIT.
                await storage.delete(
                    version.parsed_storage_key or parsed_key(owner, document_id, version.id)
                )
        except OSError as exc:
            raise RetryableJobError(
                "Chưa thể dọn file tài liệu.", error_code="document_cleanup_io"
            ) from exc
        await _verify_cleanup_claim(session, context, owner, document_id, lock=True)
        await session.delete(document)
        await session.commit()
    return {"deleted": True}


async def _verify_cleanup_claim(session, context, owner, document_id, *, lock=False):
    from backend.background.queue import BackgroundJobOwnershipError
    from backend.background.worker import JobCancellationRequested

    statement = select(BackgroundJob).where(BackgroundJob.id == context.job.id)
    if lock:
        statement = statement.with_for_update()
    job = await session.scalar(statement.execution_options(populate_existing=True))
    if (
        job is None
        or job.user_id != owner
        or job.job_type != "documents.cleanup"
        or job.payload.get("document_id") != str(document_id)
        or job.status != "running"
        or job.worker_id != context.job.worker_id
        or job.started_at != context.job.started_at
        or job.attempt_count != context.job.attempt_count
        or job.lease_expires_at is None
        or job.lease_expires_at <= datetime.now(UTC)
    ):
        raise BackgroundJobOwnershipError("Document cleanup lease is no longer owned")
    if job.cancel_requested_at is not None:
        raise JobCancellationRequested("Đã yêu cầu dừng dọn tài liệu.")
