"""Owner-scoped immutable working files, separate from RAG ingestion."""

import asyncio
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from starlette.responses import FileResponse

from artifact_runtime.protocol import ArtifactInput, ArtifactRunRequest
from backend.core.config import get_settings
from backend.dependencies import CurrentUser, DbSession
from backend.models import Document, DocumentVersion, User
from backend.models.artifact import Artifact, ArtifactVersion
from backend.routes.documents import DocumentUploadLimitMiddleware
from backend.schemas.artifact import ArtifactSelection, DocumentImport, PublishedArtifact
from backend.services.artifacts.runner_client import ArtifactRunnerClient
from backend.services.artifacts.selection import selected_versions, set_conversation_artifacts
from backend.services.artifacts.service import ArtifactService, get_artifact_service, published
from backend.services.artifacts.storage import MIME_TYPES, XLSX_MIME, ArtifactStorageError
from backend.services.artifacts.validation import ValidatedArtifact, validate_archive, validate_name
from backend.services.documents import document_storage


async def enabled(request: Request, response: Response):
    if not get_settings().workspace_artifacts_enabled:
        raise HTTPException(404, "Artifact module is disabled.")
    response.headers["Cache-Control"] = "no-store"
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site writes are not allowed.")
        if origin:
            actual = urlsplit(str(request.base_url))
            supplied = urlsplit(origin)
            if (actual.scheme, actual.netloc) != (supplied.scheme, supplied.netloc):
                raise HTTPException(403, "Cross-origin writes are not allowed.")


router = APIRouter(tags=["artifacts"], dependencies=[Depends(enabled)])
Service = Annotated[ArtifactService, Depends(get_artifact_service)]


class ArtifactUploadLimitMiddleware(DocumentUploadLimitMiddleware):
    def __init__(self, app, *, max_body_bytes):
        super().__init__(
            app,
            max_body_bytes=max_body_bytes,
            path="/api/artifacts",
            error_code="artifact_too_large",
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (
            (scope["method"] == "POST" and scope["path"] == "/api/artifacts/import-document")
            or (
                scope["method"] == "PUT"
                and re.fullmatch(r"/api/conversations/[^/]+/artifacts", scope["path"])
            )
        ):
            limiter = DocumentUploadLimitMiddleware(
                self.app,
                max_body_bytes=65536,
                path=scope["path"],
                methods=("POST", "PUT"),
                error_code="artifact_too_large",
            )
            await limiter(scope, receive, send)
        else:
            await super().__call__(scope, receive, send)


def runner_client(service: ArtifactService, owner: UUID) -> ArtifactRunnerClient:
    settings = get_settings()
    key = settings.artifact_runner_key.get_secret_value() if settings.artifact_runner_key else ""
    if len(key) < 32:
        raise HTTPException(503, "Artifact runner is not configured.")
    return ArtifactRunnerClient(
        settings.artifact_runner_url, key=key, storage=service.storage, owner=owner
    )


def view(row: ArtifactVersion) -> dict:
    return {
        **published(row).model_dump(mode="json"),
        "version_number": row.version_number,
        "origin": row.origin,
        "created_at": row.created_at.isoformat(),
        "can_edit": row.mime_type == XLSX_MIME,
        "source_run_id": str(row.source_run_id) if row.source_run_id else None,
    }


@router.post("/api/artifacts", response_model=PublishedArtifact, status_code=201)
async def upload_artifact(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    service: Service,
    file: Annotated[UploadFile, File()],
):
    name = validate_name(file.filename or "")
    extension = Path(name).suffix.lower()
    if extension not in {".xlsx", ".docx"}:
        raise HTTPException(415, "Supported uploads: XLSX and DOCX.")
    if (file.content_type or "").split(";", 1)[0] not in {
        MIME_TYPES[extension[1:]],
        "application/octet-stream",
        "",
    }:
        raise HTTPException(415, "Loại nội dung không khớp file Excel.")
    stored = await asyncio.to_thread(
        service.storage.put,
        file.file,
        f"{user.id}/{uuid4()}/{uuid4()}/content{extension}",
        max_bytes=service.max_file_bytes,
    )
    publication_started = False
    try:
        settings = get_settings()
        await asyncio.to_thread(
            validate_archive,
            service.storage.path(stored.key),
            max_unpacked_bytes=settings.artifact_max_unpacked_bytes,
            max_entries=settings.artifact_max_archive_entries,
        )

        async def cancel():
            if await request.is_disconnected():
                raise asyncio.CancelledError()

        with service.storage.path(stored.key).open("rb") as source:
            input = ArtifactInput(
                "input-0" + extension, stored.mime_type, stored.size_bytes, stored.sha256, source
            )
            report = await runner_client(service, user.id).execute(
                ArtifactRunRequest(job_id=uuid4(), action="inspect", inputs=(input.descriptor(),)),
                (input,),
                cancel=cancel,
            )
        await cancel()
        staged = ValidatedArtifact(
            stored=stored,
            name=name,
            origin="upload",
            calculation_status=report.manifest.calculation.status,
            preview_manifest=report.manifest.preview_manifest,
        )
        publication_started = True
        return await service.publish_version(
            session,
            user.id,
            artifact_id=None,
            parent_version_id=None,
            staged=staged,
            operation_key=f"upload:{uuid4()}",
        )
    except httpx.HTTPError as error:
        raise HTTPException(503, "Artifact runner chưa sẵn sàng. Hãy thử lại.") from error
    except ArtifactStorageError:
        raise
    except ValueError as error:
        raise HTTPException(422, str(error)[-1000:]) from error
    finally:
        if not publication_started:
            await asyncio.to_thread(service.storage.delete, stored.key)


@router.post("/api/artifacts/import-document", response_model=PublishedArtifact, status_code=201)
async def import_document(
    data: DocumentImport, session: DbSession, user: CurrentUser, service: Service
):
    # Document cleanup uses this same owner lock, so its private bytes cannot disappear mid-copy.
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    source = await session.scalar(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DocumentVersion.id == data.document_version_id,
            DocumentVersion.owner_user_id == user.id,
            Document.owner_user_id == user.id,
            Document.deleted_at.is_(None),
        )
    )
    if source is None:
        raise HTTPException(404, "Document version not found.")
    name = validate_name(source.original_filename)
    extension = Path(name).suffix.lower()[1:]
    if source.mime_type != MIME_TYPES[extension]:
        raise HTTPException(415, "Unsupported source MIME type.")
    path = document_storage(get_settings()).download_path(source.storage_key)
    with path.open("rb") as input:
        stored = await asyncio.to_thread(
            service.storage.put,
            input,
            f"{user.id}/{uuid4()}/{uuid4()}/content.{extension}",
            max_bytes=service.max_file_bytes,
        )
    publication_started = False
    try:
        if stored.sha256 != source.file_sha256 or stored.size_bytes != source.file_size_bytes:
            raise ArtifactStorageError("Source document checksum mismatch.")
        staged = ValidatedArtifact(
            stored=stored,
            name=name,
            origin="document",
            source_document_id=source.document_id,
            source_document_version_id=source.id,
            preview_manifest={"kind": "source_copy", "can_edit": extension == "xlsx"},
            companion_sources=[
                {
                    "role": "rag_provenance",
                    "document_id": str(source.document_id),
                    "document_version_id": str(source.id),
                    "sha256": source.file_sha256,
                }
            ],
        )
        publication_started = True
        return await service.publish_version(
            session,
            user.id,
            artifact_id=None,
            parent_version_id=None,
            staged=staged,
            operation_key=f"document-import:{source.id}",
        )
    finally:
        if not publication_started:
            await asyncio.to_thread(service.storage.delete, stored.key)


@router.get("/api/artifacts")
async def list_artifacts(
    session: DbSession,
    user: CurrentUser,
    service: Service,
    q: Annotated[str | None, Query(max_length=255)] = None,
    file_type: Literal["xlsx", "docx", "pdf", "txt"] | None = None,
    origin: Literal["upload", "agent", "document"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    conditions = [
        ArtifactVersion.owner_user_id == user.id,
        Artifact.owner_user_id == user.id,
        ArtifactVersion.version_number == Artifact.next_version_number - 1,
    ]
    if q:
        pattern = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(ArtifactVersion.name.ilike(f"%{pattern}%", escape="\\"))
    if file_type:
        conditions.append(ArtifactVersion.mime_type == MIME_TYPES[file_type])
    if origin:
        conditions.append(ArtifactVersion.origin == origin)
    statement = (
        select(ArtifactVersion)
        .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
        .where(*conditions)
    )
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = (
        await session.scalars(
            statement.order_by(ArtifactVersion.created_at.desc(), ArtifactVersion.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    used = await session.scalar(
        select(func.coalesce(func.sum(ArtifactVersion.size_bytes), 0)).where(
            ArtifactVersion.owner_user_id == user.id
        )
    )
    return {
        "items": [view(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "usage_bytes": used,
        "max_user_bytes": service.max_user_bytes,
    }


@router.get("/api/artifacts/{artifact_id}/versions")
async def versions(
    artifact_id: UUID,
    session: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    artifact = await session.scalar(
        select(Artifact.id).where(Artifact.id == artifact_id, Artifact.owner_user_id == user.id)
    )
    if artifact is None:
        raise HTTPException(404, "Artifact not found.")
    query = select(ArtifactVersion).where(
        ArtifactVersion.artifact_id == artifact_id, ArtifactVersion.owner_user_id == user.id
    )
    count = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.scalars(
            query.order_by(ArtifactVersion.version_number.desc()).limit(limit).offset(offset)
        )
    ).all()
    return {"items": [view(row) for row in rows], "total": count, "limit": limit, "offset": offset}


@router.get("/api/artifact-versions/{version_id}/download")
async def download(version_id: UUID, session: DbSession, user: CurrentUser, service: Service):
    row = await service.open_owned_version(session, user.id, version_id)
    path = service.storage.path(row.storage_key)
    if not path.is_file():
        raise HTTPException(404, "Artifact file not found.")
    return FileResponse(
        path,
        media_type=row.mime_type,
        filename=row.name,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get("/api/artifact-versions/{version_id}/preview")
async def preview_artifact(
    version_id: UUID,
    request: Request,
    session: DbSession,
    user: CurrentUser,
    service: Service,
    sheet: Annotated[str | None, Query(max_length=31)] = None,
    offset: Annotated[int, Query(ge=0, le=1048575)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    row = await service.open_owned_version(session, user.id, version_id)
    manifest = row.preview_manifest
    if row.mime_type == XLSX_MIME and (sheet is not None or offset or limit != 100 or not manifest):

        async def cancel():
            if await request.is_disconnected():
                raise asyncio.CancelledError()

        try:
            with service.storage.path(row.storage_key).open("rb") as source:
                input = ArtifactInput(
                    "input-0.xlsx", row.mime_type, row.size_bytes, row.sha256, source
                )
                result = await runner_client(service, user.id).execute(
                    ArtifactRunRequest(
                        job_id=uuid4(),
                        action="inspect",
                        inputs=(input.descriptor(),),
                        preview_sheet=sheet,
                        preview_offset=offset,
                        preview_limit=limit,
                    ),
                    (input,),
                    cancel=cancel,
                )
                manifest = result.manifest.preview_manifest
        except httpx.HTTPError as error:
            raise HTTPException(503, "Preview runner chưa sẵn sàng.") from error
        except ValueError as error:
            raise HTTPException(422, str(error)[-1000:]) from error
    from backend.models import AgentRun

    conversation = (
        await session.scalar(
            select(AgentRun.conversation_id).where(AgentRun.id == row.source_run_id)
        )
        if row.source_run_id
        else None
    )
    if conversation is None:
        from backend.models.artifact import MessageArtifact

        conversation = await session.scalar(
            select(MessageArtifact.conversation_id)
            .where(MessageArtifact.version_id == row.id, MessageArtifact.owner_user_id == user.id)
            .limit(1)
        )
    return {
        **view(row),
        "preview": manifest,
        "source_conversation_id": str(conversation) if conversation else None,
    }


@router.get("/api/conversations/{conversation_id}/artifacts")
async def selected(conversation_id: UUID, session: DbSession, user: CurrentUser):
    return {
        "items": [view(row) for row in await selected_versions(session, user.id, conversation_id)]
    }


@router.put("/api/conversations/{conversation_id}/artifacts")
async def select_artifacts(
    conversation_id: UUID, data: ArtifactSelection, session: DbSession, user: CurrentUser
):
    refs = await set_conversation_artifacts(session, user.id, conversation_id, data.version_ids)
    return {"items": [item.model_dump(mode="json") for item in refs]}
