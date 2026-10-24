"""Private document library, versioned chunks and persistent citation bindings.

Freeze the schema in this migration; do not import application models at runtime.
Vector/FTS query services and ingestion publication checks are implemented later.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "20261024_0026"
down_revision = "20261019_0025"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.create_unique_constraint("uq_conversation_owner", "conversations", ["id", "user_id"])
    op.create_unique_constraint("uq_message_conversation", "messages", ["id", "conversation_id"])
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="uploaded", nullable=False),
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
        sa.Column("revision", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR status = 'deleting'", name="ck_document_deleted_state"
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'queued', 'parsing', 'chunking', 'embedding', "
            "'indexing', 'ready', 'failed', 'cancelled', 'deleting')",
            name="ck_document_status",
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_document_title"),
        sa.CheckConstraint("revision >= 1", name="ck_document_revision"),
        sa.CheckConstraint(
            "status <> 'ready' OR active_version_id IS NOT NULL", name="ck_document_ready_version"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_document_owner"),
    )
    op.create_index(
        "ix_documents_owner_status", "documents", ["owner_user_id", "status"], unique=False
    )
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=8), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("parsed_storage_key", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="uploaded", nullable=False),
        sa.Column("pipeline_hash", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=160), nullable=True),
        sa.Column("chunker_version", sa.String(length=160), nullable=True),
        sa.Column(
            "embedding_model",
            sa.String(length=255),
            server_default="codefuse-ai/F2LLM-v2-0.6B",
            nullable=False,
        ),
        sa.Column("embedding_revision", sa.String(length=160), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), server_default="1024", nullable=False),
        sa.Column("chunk_size", sa.Integer(), server_default="512", nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), server_default="64", nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_config",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("file_sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_file_sha256"),
        sa.CheckConstraint(
            "file_type IN ('pdf', 'doc', 'docx', 'txt')", name="ck_document_file_type"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(pipeline_config) = 'object'", name="ck_document_pipeline_object"
        ),
        sa.CheckConstraint("jsonb_typeof(warnings) = 'array'", name="ck_document_warnings_array"),
        sa.CheckConstraint(
            "pipeline_hash IS NULL OR pipeline_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_pipeline_hash",
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR (chunk_count > 0 AND pipeline_hash IS NOT NULL "
            "AND parser_version IS NOT NULL AND chunker_version IS NOT NULL "
            "AND embedding_revision IS NOT NULL)",
            name="ck_document_ready_metadata",
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'queued', 'parsing', 'chunking', 'embedding', "
            "'indexing', 'ready', 'failed', 'cancelled')",
            name="ck_document_version_status",
        ),
        sa.CheckConstraint("chunk_count >= 0", name="ck_document_chunk_count"),
        sa.CheckConstraint(
            "chunk_size > 0 AND chunk_overlap >= 0 AND chunk_overlap < chunk_size",
            name="ck_document_chunk_config",
        ),
        sa.CheckConstraint("embedding_dimensions = 1024", name="ck_document_embedding_dimensions"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_document_file_size"),
        sa.CheckConstraint("page_count IS NULL OR page_count >= 1", name="ck_document_page_count"),
        sa.CheckConstraint("version_number >= 1", name="ck_document_version_number"),
        sa.ForeignKeyConstraint(
            ["document_id", "owner_user_id"],
            ["documents.id", "documents.owner_user_id"],
            name="fk_document_version_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "pipeline_hash", name="uq_document_version_pipeline"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        sa.UniqueConstraint("id", "document_id", "owner_user_id", name="uq_document_version_owner"),
    )
    op.create_index(
        "ix_document_versions_owner_hash",
        "document_versions",
        ["owner_user_id", "file_sha256"],
        unique=False,
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding_text", sa.Text(), nullable=False),
        sa.Column("lexical_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding", VECTOR(1024), nullable=True),
        sa.Column(
            "search_tsv",
            postgresql.TSVECTOR(),
            server_default=sa.text("''::tsvector"),
            nullable=False,
        ),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=1000), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_chunk_sha256"),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'", name="ck_document_chunk_metadata"
        ),
        sa.CheckConstraint(
            "(page_start IS NULL AND page_end IS NULL) OR "
            "(page_start IS NOT NULL AND page_end IS NOT NULL "
            "AND page_start >= 1 AND page_end >= page_start)",
            name="ck_document_chunk_pages",
        ),
        sa.CheckConstraint(
            "(start_offset IS NULL AND end_offset IS NULL) OR "
            "(start_offset IS NOT NULL AND end_offset IS NOT NULL "
            "AND start_offset >= 0 AND end_offset > start_offset)",
            name="ck_document_chunk_offsets",
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_document_chunk_position"),
        sa.CheckConstraint("length(btrim(text)) > 0", name="ck_document_chunk_text"),
        sa.CheckConstraint(
            "page_start IS NOT NULL OR start_offset IS NOT NULL "
            "OR (section IS NOT NULL AND length(btrim(section)) > 0)",
            name="ck_document_chunk_provenance",
        ),
        sa.CheckConstraint("token_count > 0", name="ck_document_chunk_tokens"),
        sa.ForeignKeyConstraint(
            ["version_id", "document_id", "owner_user_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.owner_user_id",
            ],
            name="fk_document_chunk_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "version_id", "document_id", "owner_user_id", name="uq_document_chunk_owner"
        ),
        sa.UniqueConstraint("version_id", "chunk_index", name="uq_document_chunk_position"),
    )
    op.create_index(
        "ix_document_chunks_scope",
        "document_chunks",
        ["owner_user_id", "document_id", "version_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_search",
        "document_chunks",
        ["search_tsv"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "document_ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("background_job_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("stage", sa.String(length=20), server_default="parsing", nullable=False),
        sa.Column("completed_chunks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage IN ('parsing', 'chunking', 'embedding', 'indexing')",
            name="ck_document_ingestion_stage",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_document_ingestion_status",
        ),
        sa.CheckConstraint(
            "completed_chunks >= 0 AND (total_chunks IS NULL "
            "OR (total_chunks >= 0 AND completed_chunks <= total_chunks))",
            name="ck_document_ingestion_progress",
        ),
        sa.ForeignKeyConstraint(["background_job_id"], ["background_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["version_id", "document_id", "owner_user_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.owner_user_id",
            ],
            name="fk_document_ingestion_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", name="uq_document_ingestion_version"),
    )
    op.create_index(
        op.f("ix_document_ingestion_jobs_background_job_id"),
        "document_ingestion_jobs",
        ["background_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_ingestion_owner_status",
        "document_ingestion_jobs",
        ["owner_user_id", "status"],
        unique=False,
    )
    op.create_table(
        "conversation_documents",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "owner_user_id"],
            ["conversations.id", "conversations.user_id"],
            name="fk_conversation_document_conversation_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "owner_user_id"],
            ["documents.id", "documents.owner_user_id"],
            name="fk_conversation_document_document_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", "document_id"),
    )
    op.create_index(
        "ix_conversation_documents_document",
        "conversation_documents",
        ["document_id", "owner_user_id"],
        unique=False,
    )
    op.create_table(
        "document_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("citation_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_config_version", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(citation_id)) > 0", name="ck_document_citation_label"),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["conversation_id", "owner_user_id"],
            ["conversations.id", "conversations.user_id"],
            name="fk_document_citation_conversation_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_document_citation_message",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "citation_id", name="uq_document_citation_message_label"),
    )
    op.create_index(
        "ix_document_citations_conversation",
        "document_citations",
        ["conversation_id", "owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_citations_source",
        "document_citations",
        ["chunk_id", "version_id", "document_id", "owner_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_document_active_version",
        "documents",
        "document_versions",
        ["active_version_id", "id", "owner_user_id"],
        ["id", "document_id", "owner_user_id"],
        initially="DEFERRED",
        deferrable=True,
    )


def downgrade():
    op.drop_constraint("fk_document_active_version", "documents", type_="foreignkey")
    op.drop_table("document_citations")
    op.drop_table("conversation_documents")
    op.drop_table("document_ingestion_jobs")
    op.drop_table("document_chunks")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_constraint("uq_message_conversation", "messages", type_="unique")
    op.drop_constraint("uq_conversation_owner", "conversations", type_="unique")
    # Extensions may be shared by other features; leave them installed.
