from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base

ACTIVE_STATUSES = ("queued", "running", "waiting_approval", "cancelling", "needs_reconciliation")


class Automation(Base):
    __tablename__ = "automations"
    __table_args__ = (UniqueConstraint("id", "owner_id", name="uq_automation_owner"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    revision_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AutomationRevision(Base):
    __tablename__ = "automation_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["automation_id", "owner_id"],
            ["automations.id", "automations.owner_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("automation_id", "number", name="uq_automation_revision_number"),
        UniqueConstraint("id", "automation_id", "owner_id", name="uq_automation_revision_owner"),
        CheckConstraint("number >= 1", name="ck_automation_revision_number"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    automation_id: Mapped[UUID] = mapped_column()
    owner_id: Mapped[UUID] = mapped_column()
    number: Mapped[int] = mapped_column(Integer)
    instruction: Mapped[str] = mapped_column(Text)
    schedule: Mapped[dict] = mapped_column(JSONB)
    artifact_version_ids: Mapped[list] = mapped_column(JSONB)
    artifact_actions: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AutomationGrant(Base):
    __tablename__ = "automation_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "owner_id"],
            ["google_connections.id", "google_connections.owner_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["revision_id", "automation_id", "owner_id"],
            [
                "automation_revisions.id",
                "automation_revisions.automation_id",
                "automation_revisions.owner_id",
            ],
            ondelete="CASCADE",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(index=True)
    automation_id: Mapped[UUID] = mapped_column()
    owner_id: Mapped[UUID] = mapped_column()
    connection_id: Mapped[UUID] = mapped_column()
    file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    range_a1: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sheet_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connection_generation: Mapped[int] = mapped_column(Integer)
    grant_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "automation_id", "owner_id"],
            [
                "automation_revisions.id",
                "automation_revisions.automation_id",
                "automation_revisions.owner_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("trigger IN ('scheduled','manual')", name="ck_automation_run_trigger"),
        CheckConstraint(
            "status IN ('queued','running','waiting_approval','cancelling',"
            "'needs_reconciliation','succeeded','failed','cancelled','skipped_overlap','missed')",
            name="ck_automation_run_status",
        ),
        UniqueConstraint("automation_id", "scheduled_for", name="uq_automation_tick"),
        UniqueConstraint("automation_id", "idempotency_key", name="uq_automation_manual"),
        Index(
            "uq_automation_active",
            "automation_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','running','waiting_approval','cancelling',"
                "'needs_reconciliation')"
            ),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    automation_id: Mapped[UUID] = mapped_column(index=True)
    owner_id: Mapped[UUID] = mapped_column(index=True)
    revision_id: Mapped[UUID] = mapped_column()
    trigger: Mapped[str] = mapped_column(String(16))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="SET NULL"), nullable=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    stop_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
