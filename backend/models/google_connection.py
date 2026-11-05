"""Google authority and encrypted credentials, independent of MCP plugin bindings."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class GoogleConnection(Base):
    __tablename__ = "google_connections"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_google_connection_owner"),
        UniqueConstraint("owner_id", "provider", name="uq_google_owner_provider"),
        CheckConstraint(
            "status IN ('connected','disconnected','reconnect')", name="ck_google_status"
        ),
        CheckConstraint("generation >= 1", name="ck_google_generation"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16), default="google", server_default="google")
    status: Mapped[str] = mapped_column(String(16), default="disconnected")
    generation: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    account_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    granted_scopes: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    offline_ready: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GoogleOAuthState(Base):
    __tablename__ = "google_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GoogleFileGrant(Base):
    __tablename__ = "google_file_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "owner_id"],
            ["google_connections.id", "google_connections.owner_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("connection_id", "file_id", name="uq_google_grant_file"),
        UniqueConstraint("id", "owner_id", "connection_id", name="uq_google_grant_owner"),
        CheckConstraint("generation >= 1", name="ck_google_grant_generation"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column()
    connection_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128))
    can_edit: Mapped[bool] = mapped_column(Boolean)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    connection_generation: Mapped[int] = mapped_column(Integer)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class GoogleWorkFile(Base):
    __tablename__ = "google_work_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["context_id", "owner_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["grant_id", "owner_id", "connection_id"],
            [
                "google_file_grants.id",
                "google_file_grants.owner_id",
                "google_file_grants.connection_id",
            ],
            ondelete="CASCADE",
        ),
    )
    context_id: Mapped[UUID] = mapped_column(primary_key=True)
    grant_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID] = mapped_column()
    connection_id: Mapped[UUID] = mapped_column()
    grant_generation: Mapped[int] = mapped_column(Integer)


class GoogleMutation(Base):
    __tablename__ = "google_mutations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "owner_id"],
            ["google_connections.id", "google_connections.owner_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["context_id", "owner_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("owner_id", "operation_key", name="uq_google_mutation_operation"),
        CheckConstraint(
            "status IN ('prepared','approved','sent','completed','unknown','rejected')",
            name="ck_google_mutation_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column()
    connection_id: Mapped[UUID] = mapped_column()
    context_id: Mapped[UUID] = mapped_column()
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    operation_key: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(40))
    file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    grant_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connection_generation: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="prepared")
    receipt: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
