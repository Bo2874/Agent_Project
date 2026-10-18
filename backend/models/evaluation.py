import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name="ck_eval_runs_status",
        ),
        CheckConstraint("total_cases >= 1", name="ck_eval_runs_total_cases"),
        CheckConstraint(
            "completed_cases BETWEEN 0 AND total_cases",
            name="ck_eval_runs_completed_cases",
        ),
        Index(
            "ix_eval_runs_version_lookup",
            "dataset_id",
            "dataset_version",
            "model_name",
            "prompt_version",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[str] = mapped_column(String(80), index=True)
    dataset_version: Mapped[str] = mapped_column(String(32))
    dataset_fingerprint: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(100), index=True)
    prompt_version: Mapped[str] = mapped_column(String(80), index=True)
    prompt_fingerprint: Mapped[str] = mapped_column(String(64))
    judge_provider: Mapped[str] = mapped_column(String(40))
    judge_model_name: Mapped[str] = mapped_column(String(100))
    judge_prompt_version: Mapped[str] = mapped_column(String(80))
    judge_prompt_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(20), default="running", server_default="running", index=True
    )
    total_cases: Mapped[int] = mapped_column(Integer)
    completed_cases: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    selected_case_ids: Mapped[list[str]] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalCaseResult(Base):
    __tablename__ = "eval_case_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('passed', 'failed', 'error', 'skipped')",
            name="ck_eval_case_results_status",
        ),
        CheckConstraint(
            "category IN ('chat', 'tool', 'rag', 'safety')",
            name="ck_eval_case_results_category",
        ),
        CheckConstraint(
            "actual_outcome IS NULL OR actual_outcome IN "
            "('answer', 'clarify', 'abstain', 'refuse')",
            name="ck_eval_case_results_outcome",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_eval_case_results_score",
        ),
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0 "
            "AND judge_tokens >= 0 AND latency_ms >= 0",
            name="ck_eval_case_results_metrics",
        ),
        UniqueConstraint("eval_run_id", "case_id", name="uq_eval_case_results_run_case"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    critical_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    actual_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    citation_sources: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    criterion_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    judge_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
