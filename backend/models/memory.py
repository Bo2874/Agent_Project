import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        CheckConstraint(
            "memory_type IN ('fact', 'preference', 'goal', 'constraint')",
            name="ck_user_memories_type",
        ),
        CheckConstraint(
            "source_type IN ('user_message', 'tool_result', 'manual', 'import')",
            name="ck_user_memories_source_type",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')",
            name="ck_user_memories_status",
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'personal', 'sensitive')",
            name="ck_user_memories_sensitivity",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_user_memories_confidence",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from",
            name="ck_user_memories_valid_range",
        ),
        CheckConstraint(
            "(source_type = 'user_message' AND source_message_id IS NOT NULL "
            "AND source_tool_call_id IS NULL) OR "
            "(source_type = 'tool_result' AND source_tool_call_id IS NOT NULL "
            "AND source_message_id IS NULL) OR "
            "(source_type IN ('manual', 'import') AND source_message_id IS NULL "
            "AND source_tool_call_id IS NULL)",
            name="ck_user_memories_source_link",
        ),
        Index("ix_user_memories_user_type_status", "user_id", "memory_type", "status"),
        Index("ix_user_memories_user_key", "user_id", "memory_key"),
        Index("ix_user_memories_user_retention", "user_id", "retention_until"),
        Index(
            "uq_user_memories_active_key",
            "user_id",
            "memory_type",
            "memory_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    memory_type: Mapped[str] = mapped_column(String(24))
    memory_key: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(24))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True
    )
    source_tool_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="CASCADE"), nullable=True
    )
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), default=Decimal("1.000"), server_default=text("1.000")
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    sensitivity: Mapped[str] = mapped_column(
        String(20), default="personal", server_default="personal"
    )
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_memories.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
