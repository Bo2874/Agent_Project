from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.models import (
    AgentRun,
    AuditLog,
    BackgroundJob,
    EvalCaseResult,
    EvalRun,
    ModelCall,
    ToolCall,
)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    error_days: int
    tool_payload_days: int
    agent_run_days: int
    background_job_days: int
    audit_log_days: int


@dataclass(frozen=True, slots=True)
class RetentionReport:
    tool_payloads_cleared: int
    error_messages_cleared: int
    agent_runs_deleted: int
    background_jobs_deleted: int
    audit_logs_deleted: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def configured_retention_policy() -> RetentionPolicy:
    settings = get_settings()
    return RetentionPolicy(
        error_days=settings.operational_error_retention_days,
        tool_payload_days=settings.tool_payload_retention_days,
        agent_run_days=settings.agent_run_retention_days,
        background_job_days=settings.background_job_retention_days,
        audit_log_days=settings.audit_log_retention_days,
    )


def _affected_rows(result: object) -> int:
    return max(int(getattr(result, "rowcount", 0) or 0), 0)


async def purge_expired_operational_data(
    session: AsyncSession,
    *,
    policy: RetentionPolicy | None = None,
    now: datetime | None = None,
    apply_changes: bool = True,
) -> RetentionReport:
    active_policy = policy or configured_retention_policy()
    current_time = now or datetime.now(UTC)
    tool_cutoff = current_time - timedelta(days=active_policy.tool_payload_days)
    error_cutoff = current_time - timedelta(days=active_policy.error_days)

    cleared_payloads = await session.execute(
        update(ToolCall)
        .where(
            ToolCall.created_at < tool_cutoff,
            or_(
                ToolCall.arguments != {},
                ToolCall.result_content.is_not(None),
                ToolCall.result_data.is_not(None),
            ),
        )
        .values(arguments={}, result_content=None, result_data=None)
    )

    error_results = []
    for model in (AgentRun, ModelCall, ToolCall, BackgroundJob, EvalRun, EvalCaseResult):
        error_results.append(
            await session.execute(
                update(model)
                .where(model.created_at < error_cutoff, model.error_message.is_not(None))
                .values(error_message=None)
            )
        )

    deleted_runs = await session.execute(
        delete(AgentRun).where(
            AgentRun.status.in_(("completed", "failed", "cancelled")),
            AgentRun.finished_at < current_time - timedelta(days=active_policy.agent_run_days),
        )
    )
    deleted_jobs = await session.execute(
        delete(BackgroundJob).where(
            BackgroundJob.status.in_(("succeeded", "failed", "cancelled")),
            BackgroundJob.finished_at
            < current_time - timedelta(days=active_policy.background_job_days),
        )
    )
    deleted_audits = await session.execute(
        delete(AuditLog).where(
            AuditLog.created_at < current_time - timedelta(days=active_policy.audit_log_days)
        )
    )
    if apply_changes:
        await session.commit()
    else:
        await session.rollback()
    return RetentionReport(
        tool_payloads_cleared=_affected_rows(cleared_payloads),
        error_messages_cleared=sum(_affected_rows(item) for item in error_results),
        agent_runs_deleted=_affected_rows(deleted_runs),
        background_jobs_deleted=_affected_rows(deleted_jobs),
        audit_logs_deleted=_affected_rows(deleted_audits),
    )
