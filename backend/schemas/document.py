from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

DocumentStatus = Literal[
    "uploaded",
    "queued",
    "parsing",
    "chunking",
    "embedding",
    "indexing",
    "ready",
    "failed",
    "cancelled",
    "deleting",
]
DocumentType = Literal["pdf", "doc", "docx", "txt"]


class DocumentResponse(BaseModel):
    id: UUID
    title: str
    status: DocumentStatus
    revision: int
    active_version_id: UUID | None
    version_id: UUID
    version_number: int
    original_filename: str
    file_type: DocumentType
    mime_type: str
    file_size_bytes: int
    file_sha256: str
    page_count: int | None
    chunk_count: int
    warnings: list[dict[str, Any]]
    error_code: str | None
    error_message: str | None
    ready_for_qa: bool
    deduplicated: bool = False
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class IngestionProgressResponse(BaseModel):
    status: str
    stage: str
    completed_chunks: int
    total_chunks: int | None
    error_code: str | None
    error_message: str | None


class DocumentDetailResponse(DocumentResponse):
    ingestion: IngestionProgressResponse | None = None


class DocumentUsageResponse(BaseModel):
    used_bytes: int
    document_count: int
    max_file_bytes: int
    max_user_bytes: int
    max_documents: int


class DocumentDeleteResponse(BaseModel):
    id: UUID
    status: Literal["deleting"] = "deleting"
    message: str = "Tài liệu đã ngừng cho phép truy cập và đang được dọn dữ liệu."
