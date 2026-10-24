"""Private document library and versioned RAG data; retrieval still needs owner filters."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class DocumentTimestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Document(DocumentTimestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="uq_document_owner"),
        ForeignKeyConstraint(
            ["active_version_id", "id", "owner_user_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.owner_user_id",
            ],
            name="fk_document_active_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("length(btrim(title)) > 0", name="ck_document_title"),
        CheckConstraint(
            "status IN ('uploaded', 'queued', 'parsing', 'chunking', 'embedding', "
            "'indexing', 'ready', 'failed', 'cancelled', 'deleting')",
            name="ck_document_status",
        ),
        CheckConstraint("revision >= 1", name="ck_document_revision"),
        CheckConstraint(
            "status <> 'ready' OR active_version_id IS NOT NULL", name="ck_document_ready_version"
        ),
        CheckConstraint(
            "deleted_at IS NULL OR status = 'deleting'", name="ck_document_deleted_state"
        ),
        Index("ix_documents_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="uploaded", server_default="uploaded")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentVersion(DocumentTimestamps, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "owner_user_id"],
            ["documents.id", "documents.owner_user_id"],
            name="fk_document_version_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "document_id", "owner_user_id", name="uq_document_version_owner"),
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        UniqueConstraint("document_id", "pipeline_hash", name="uq_document_version_pipeline"),
        CheckConstraint("version_number >= 1", name="ck_document_version_number"),
        CheckConstraint("file_type IN ('pdf', 'doc', 'docx', 'txt')", name="ck_document_file_type"),
        CheckConstraint("file_size_bytes > 0", name="ck_document_file_size"),
        CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_file_sha256"),
        CheckConstraint(
            "pipeline_hash IS NULL OR pipeline_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_pipeline_hash",
        ),
        CheckConstraint(
            "status IN ('uploaded', 'queued', 'parsing', 'chunking', 'embedding', "
            "'indexing', 'ready', 'failed', 'cancelled')",
            name="ck_document_version_status",
        ),
        CheckConstraint("page_count IS NULL OR page_count >= 1", name="ck_document_page_count"),
        CheckConstraint("chunk_count >= 0", name="ck_document_chunk_count"),
        CheckConstraint(
            "chunk_size > 0 AND chunk_overlap >= 0 AND chunk_overlap < chunk_size",
            name="ck_document_chunk_config",
        ),
        CheckConstraint("embedding_dimensions = 1024", name="ck_document_embedding_dimensions"),
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="ck_document_warnings_array"),
        CheckConstraint(
            "jsonb_typeof(pipeline_config) = 'object'", name="ck_document_pipeline_object"
        ),
        CheckConstraint(
            "status <> 'ready' OR (chunk_count > 0 AND pipeline_hash IS NOT NULL "
            "AND parser_version IS NOT NULL AND chunker_version IS NOT NULL "
            "AND embedding_revision IS NOT NULL)",
            name="ck_document_ready_metadata",
        ),
        Index("ix_document_versions_owner_hash", "owner_user_id", "file_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column()
    owner_user_id: Mapped[uuid.UUID] = mapped_column()
    version_number: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(8))
    mime_type: Mapped[str] = mapped_column(String(128))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_sha256: Mapped[str] = mapped_column(String(64))
    # Opaque keys relative to DocumentStorage, never user-supplied absolute paths.
    storage_key: Mapped[str] = mapped_column(String(512))
    parsed_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="uploaded", server_default="uploaded")
    pipeline_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    chunker_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        String(255),
        default="codefuse-ai/F2LLM-v2-0.6B",
        server_default="codefuse-ai/F2LLM-v2-0.6B",
    )
    embedding_revision: Mapped[str | None] = mapped_column(String(160), nullable=True)
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=1024, server_default="1024")
    chunk_size: Mapped[int] = mapped_column(Integer, default=512, server_default="512")
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=64, server_default="64")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=sql_text("'[]'::jsonb")
    )
    pipeline_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "document_id", "owner_user_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.owner_user_id",
            ],
            name="fk_document_chunk_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id", "version_id", "document_id", "owner_user_id", name="uq_document_chunk_owner"
        ),
        UniqueConstraint("version_id", "chunk_index", name="uq_document_chunk_position"),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunk_position"),
        CheckConstraint("token_count > 0", name="ck_document_chunk_tokens"),
        CheckConstraint("length(btrim(text)) > 0", name="ck_document_chunk_text"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_chunk_sha256"),
        CheckConstraint(
            "(page_start IS NULL AND page_end IS NULL) OR "
            "(page_start IS NOT NULL AND page_end IS NOT NULL "
            "AND page_start >= 1 AND page_end >= page_start)",
            name="ck_document_chunk_pages",
        ),
        CheckConstraint(
            "(start_offset IS NULL AND end_offset IS NULL) OR "
            "(start_offset IS NOT NULL AND end_offset IS NOT NULL "
            "AND start_offset >= 0 AND end_offset > start_offset)",
            name="ck_document_chunk_offsets",
        ),
        CheckConstraint(
            "page_start IS NOT NULL OR start_offset IS NOT NULL "
            "OR (section IS NOT NULL AND length(btrim(section)) > 0)",
            name="ck_document_chunk_provenance",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'", name="ck_document_chunk_metadata"
        ),
        Index("ix_document_chunks_scope", "owner_user_id", "document_id", "version_id"),
        Index("ix_document_chunks_search", "search_tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column()
    document_id: Mapped[uuid.UUID] = mapped_column()
    owner_user_id: Mapped[uuid.UUID] = mapped_column()
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)  # Original passage for evidence/citation.
    embedding_text: Mapped[str] = mapped_column(Text)
    lexical_text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024), nullable=True)
    # Ingestion populates the tsvector explicitly; no language-dependent default tokenizer.
    search_tsv: Mapped[str] = mapped_column(TSVECTOR, server_default=sql_text("''::tsvector"))
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentIngestionJob(DocumentTimestamps, Base):
    """Stage progress attached to the existing background queue, not a second queue."""

    __tablename__ = "document_ingestion_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "document_id", "owner_user_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.owner_user_id",
            ],
            name="fk_document_ingestion_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("version_id", name="uq_document_ingestion_version"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_document_ingestion_status",
        ),
        CheckConstraint(
            "stage IN ('parsing', 'chunking', 'embedding', 'indexing')",
            name="ck_document_ingestion_stage",
        ),
        CheckConstraint(
            "completed_chunks >= 0 AND (total_chunks IS NULL "
            "OR (total_chunks >= 0 AND completed_chunks <= total_chunks))",
            name="ck_document_ingestion_progress",
        ),
        Index("ix_document_ingestion_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column()
    document_id: Mapped[uuid.UUID] = mapped_column()
    owner_user_id: Mapped[uuid.UUID] = mapped_column()
    background_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")
    stage: Mapped[str] = mapped_column(String(20), default="parsing", server_default="parsing")
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationDocument(Base):
    __tablename__ = "conversation_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "owner_user_id"],
            ["conversations.id", "conversations.user_id"],
            name="fk_conversation_document_conversation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "owner_user_id"],
            ["documents.id", "documents.owner_user_id"],
            name="fk_conversation_document_document_owner",
            ondelete="CASCADE",
        ),
        Index("ix_conversation_documents_document", "document_id", "owner_user_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentCitation(Base):
    """Source binding for a saved answer; source deletion also removes the binding."""

    __tablename__ = "document_citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_document_citation_message",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "owner_user_id"],
            ["conversations.id", "conversations.user_id"],
            name="fk_document_citation_conversation_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "version_id", "document_id", "owner_user_id"],
            [
                "document_chunks.id",
                "document_chunks.version_id",
                "document_chunks.document_id",
                "document_chunks.owner_user_id",
            ],
            name="fk_document_citation_source_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint("message_id", "citation_id", name="uq_document_citation_message_label"),
        CheckConstraint("length(btrim(citation_id)) > 0", name="ck_document_citation_label"),
        Index(
            "ix_document_citations_source", "chunk_id", "version_id", "document_id", "owner_user_id"
        ),
        Index("ix_document_citations_conversation", "conversation_id", "owner_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column()
    conversation_id: Mapped[uuid.UUID] = mapped_column()
    owner_user_id: Mapped[uuid.UUID] = mapped_column()
    citation_id: Mapped[str] = mapped_column(String(128))
    document_id: Mapped[uuid.UUID] = mapped_column()
    version_id: Mapped[uuid.UUID] = mapped_column()
    chunk_id: Mapped[uuid.UUID] = mapped_column()
    retrieval_config_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
