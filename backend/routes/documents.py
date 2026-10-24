from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from starlette.formparsers import MultiPartException
from starlette.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.dependencies import CurrentUser, DbSession
from backend.schemas.document import (
    DocumentDeleteResponse,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    DocumentType,
    DocumentUsageResponse,
)
from backend.services.documents import (
    delete_document,
    document_usage,
    download_document,
    get_document,
    list_documents,
    upload_document,
)

router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentUploadLimitMiddleware:
    """Bound multipart bytes before Starlette spools them, including chunked uploads."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int):
        self.app, self.max_body_bytes = app, max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != "/api/documents"
        ):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"detail": "File vượt giới hạn dung lượng.", "code": "document_too_large"},
            status_code=413,
        )
        headers = dict(scope["headers"])
        try:
            declared_size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared_size = -1
        if declared_size < 0 or declared_size > self.max_body_bytes:
            await response(scope, receive, send)
            return
        received, too_large, replied = 0, False, False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    too_large = True
                    # Starlette closes partially spooled files for this exception.
                    raise MultiPartException("File vượt giới hạn dung lượng.")
            return message

        async def limited_send(message: Message) -> None:
            nonlocal replied
            if too_large:
                if not replied:
                    replied = True
                    await response(scope, receive, send)
            else:
                await send(message)

        await self.app(scope, limited_receive, limited_send)


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def post_document(
    session: DbSession,
    user: CurrentUser,
    response: Response,
    file: Annotated[UploadFile, File()],
    conversation_id: Annotated[UUID | None, Form()] = None,
) -> DocumentResponse:
    document = await upload_document(session, user.id, file, conversation_id=conversation_id)
    if document.deduplicated:
        response.status_code = status.HTTP_200_OK
    return document


@router.get("", response_model=DocumentListResponse)
async def get_documents(
    session: DbSession,
    user: CurrentUser,
    q: Annotated[str | None, Query(max_length=255)] = None,
    file_type: DocumentType | None = None,
    status: DocumentStatus | None = None,
    include_deleting: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    return await list_documents(
        session,
        user.id,
        query=q,
        file_type=file_type,
        status=status,
        include_deleting=include_deleting,
        limit=limit,
        offset=offset,
    )


@router.get("/usage", response_model=DocumentUsageResponse)
async def get_usage(session: DbSession, user: CurrentUser) -> DocumentUsageResponse:
    return await document_usage(session, user.id)


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document_detail(
    document_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> DocumentDetailResponse:
    return await get_document(session, user.id, document_id)


@router.get("/{document_id}/status", response_model=DocumentDetailResponse)
async def get_document_status(
    document_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> DocumentDetailResponse:
    return await get_document(session, user.id, document_id)


@router.get("/{document_id}/download")
async def get_document_download(
    document_id: UUID,
    session: DbSession,
    user: CurrentUser,
    version_id: UUID | None = None,
) -> FileResponse:
    path, filename, mime_type = await download_document(session, user.id, document_id, version_id)
    return FileResponse(
        path,
        filename=filename,
        media_type=mime_type,
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.delete("/{document_id}", response_model=DocumentDeleteResponse, status_code=202)
async def remove_document(
    document_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> DocumentDeleteResponse:
    return await delete_document(session, user.id, document_id)
