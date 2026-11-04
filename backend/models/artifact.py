"""Private immutable file revisions; bytes live outside the web static directory."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="uq_artifact_owner"),
        CheckConstraint("next_version_number >= 1", name="ck_artifact_next_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    next_version_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="uq_artifact_version_owner"),
        UniqueConstraint("id", "artifact_id", "owner_user_id", name="uq_artifact_version_parent"),
        UniqueConstraint("artifact_id", "version_number", name="uq_artifact_version_number"),
        UniqueConstraint("owner_user_id", "operation_key", name="uq_artifact_operation"),
        UniqueConstraint("storage_key", name="uq_artifact_storage_key"),
        ForeignKeyConstraint(
            ["artifact_id", "owner_user_id"],
            ["artifacts.id", "artifacts.owner_user_id"],
            ondelete="CASCADE",
            name="fk_artifact_version_owner",
        ),
        ForeignKeyConstraint(
            ["parent_version_id", "artifact_id", "owner_user_id"],
            [
                "artifact_versions.id",
                "artifact_versions.artifact_id",
                "artifact_versions.owner_user_id",
            ],
            name="fk_artifact_parent",
        ),
        ForeignKeyConstraint(
            ["source_document_version_id", "source_document_id", "owner_user_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.owner_user_id",
            ],
            name="fk_artifact_source_document",
        ),
        CheckConstraint("version_number >= 1 AND size_bytes > 0", name="ck_artifact_version_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifact_sha256"),
        CheckConstraint("origin IN ('upload','agent','document')", name="ck_artifact_origin"),
        CheckConstraint(
            "calculation_status IN ('not_calculated','fresh','unsupported','failed')",
            name="ck_artifact_calculation",
        ),
        CheckConstraint(
            "(source_document_version_id IS NULL) = (source_document_id IS NULL)",
            name="ck_artifact_source_pair",
        ),
        CheckConstraint("jsonb_typeof(preview_manifest) = 'object'", name="ck_artifact_preview"),
        CheckConstraint("jsonb_typeof(companion_sources) = 'array'", name="ck_artifact_companions"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    version_number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    operation_key: Mapped[str] = mapped_column(String(160))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_document_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    calculation_status: Mapped[str] = mapped_column(
        String(20),
        default="not_calculated",
        server_default="not_calculated",
    )
    preview_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    companion_sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationArtifact(Base):
    __tablename__ = "conversation_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "owner_user_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="CASCADE",
            name="fk_conversation_artifact_owner",
        ),
        ForeignKeyConstraint(
            ["version_id", "owner_user_id"],
            ["artifact_versions.id", "artifact_versions.owner_user_id"],
            ondelete="CASCADE",
            name="fk_conversation_artifact_version",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column()


class MessageArtifact(Base):
    __tablename__ = "message_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_message_artifact_message",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "owner_user_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="CASCADE",
            name="fk_message_artifact_owner",
        ),
        ForeignKeyConstraint(
            ["version_id", "owner_user_id"],
            ["artifact_versions.id", "artifact_versions.owner_user_id"],
            ondelete="CASCADE",
            name="fk_message_artifact_version",
        ),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column()
    owner_user_id: Mapped[uuid.UUID] = mapped_column()
