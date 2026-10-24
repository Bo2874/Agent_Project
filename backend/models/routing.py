import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class RoutingTrace(Base):
    __tablename__ = "routing_traces"
    __table_args__ = (
        CheckConstraint("phase IN ('query','capability','selection')", name="ck_routing_phase"),
        CheckConstraint("mode IN ('active','shadow')", name="ck_routing_mode"),
        CheckConstraint("provider_calls >= 0 AND reserved_tokens >= 0", name="ck_routing_budget"),
        CheckConstraint("latency_ms >= 0", name="ck_routing_latency"),
        CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_routing_input"),
        CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_routing_output"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(10))
    provider: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(20))
    model_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    selected_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_calls: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
