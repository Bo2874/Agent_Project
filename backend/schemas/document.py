from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_core.contracts.retrieval import RetrievalResult

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
    reindexing: bool = False
    cleanup_status: Literal["queued", "running", "succeeded", "failed", "cancelled"] | None = None
    awaiting_embedding: bool = False
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


class DocumentCancelResponse(BaseModel):
    id: UUID
    status: Literal["cancel_requested", "cancelled"]
    message: str


class DocumentReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version_id: UUID


class ConversationDocumentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] = Field(max_length=1000)

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Danh sách tài liệu không được trùng lặp.")
        return value


class ConversationDocumentsResponse(BaseModel):
    conversation_id: UUID
    items: list[DocumentResponse]


class DocumentSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)
    document_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    conversation_id: UUID | None = None
    mode: Literal["dense", "lexical", "hybrid"] = "hybrid"
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description=(
            "Top-k for dense/lexical only; hybrid uses fixed 20+20 candidates and evidence budget."
        ),
    )

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if not self.query.strip() or "\x00" in self.query:
            raise ValueError("Câu hỏi không hợp lệ.")
        if bool(self.document_ids) == (self.conversation_id is not None):
            raise ValueError("Chọn danh sách tài liệu hoặc một hội thoại.")
        if len(self.document_ids) != len(set(self.document_ids)):
            raise ValueError("Danh sách tài liệu không được trùng.")
        return self


class DocumentRewriteTrace(BaseModel):
    status: Literal["skipped", "completed", "failed", "cancelled"] = "skipped"
    query: str
    prompt_version: str = "document-query-rewrite-v1"
    history_messages: int = 0
    history_trimmed: bool = False
    file_metadata_trimmed: bool = False
    provider: str | None = None
    model: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0


class DocumentSearchResponse(BaseModel):
    query: str
    mode: Literal["dense", "lexical", "hybrid"]
    items: list[RetrievalResult]
    retrieval_config_version: str
    rewrite: DocumentRewriteTrace | None = None
    reranker_profile: dict[str, Any] | None = None
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_estimated_tokens: int = 0
    evidence_token_budget: int | None = None
    budget_skipped_chunks: int = 0


class DocumentCitationResponse(BaseModel):
    message_id: UUID | None = None
    citation_id: str
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    title: str
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None


class DocumentCitationSourceResponse(DocumentCitationResponse):
    text: str
