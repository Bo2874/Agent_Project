import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', "
            "'completed', 'failed', 'cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("current_step >= 0", name="ck_agent_runs_current_step"),
        CheckConstraint(
            "context_total_tokens IS NULL OR context_total_tokens >= 0",
            name="ck_agent_runs_context_total_tokens",
        ),
        CheckConstraint(
            "context_trimmed_messages >= 0",
            name="ck_agent_runs_context_trimmed_messages",
        ),
        CheckConstraint("resume_count >= 0", name="ck_agent_runs_resume_count"),
        CheckConstraint(
            "rag_trace IS NULL OR jsonb_typeof(rag_trace) = 'object'",
            name="ck_agent_runs_rag_trace",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(24), default="running", server_default="running", index=True
    )
    current_step: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    event_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    stop_reason: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    context_estimator: Mapped[str | None] = mapped_column(String(40), nullable=True)
    context_system_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_status_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_memory_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_history_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_summary_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tool_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_rag_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_budget_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_trimmed_messages: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tool_selection_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    available_tool_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loaded_tool_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rag_trace: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_run_steps_status",
        ),
        CheckConstraint("step_number >= 1", name="ck_run_steps_step_number"),
        CheckConstraint("attempt_number >= 1", name="ck_run_steps_attempt_number"),
        UniqueConstraint(
            "agent_run_id",
            "step_number",
            "attempt_number",
            name="uq_run_steps_run_number_attempt",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    model_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_calls.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    step_number: Mapped[int] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'waiting_approval', 'running', "
            "'completed', 'failed', 'denied', 'cancelled')",
            name="ck_tool_calls_status",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_tool_calls_duration_ms",
        ),
        CheckConstraint(
            "approval_decision IS NULL OR approval_decision IN ('approved', 'denied')",
            name="ck_tool_calls_approval_decision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_step_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_steps.id", ondelete="CASCADE"), index=True
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    result_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, unique=True)
    approval_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
