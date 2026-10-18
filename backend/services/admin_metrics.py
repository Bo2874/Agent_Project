from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.redaction import redact_text
from backend.models import AgentRun, ModelCall, RunStep, ToolCall
from backend.schemas.admin import (
    AdminActivityMetrics,
    AdminAlert,
    AdminCacheMetrics,
    AdminErrorMetrics,
    AdminLatencyMetrics,
    AdminOverviewResponse,
    AdminRecentError,
    AdminRecentModelCall,
    AdminRecentToolCall,
    AdminTokenMetrics,
    AdminToolMetrics,
)
from backend.services.token_cost import calculate_cache_hit_rate, calculate_token_cost

RECENT_ITEM_LIMIT = 12
RUN_STATUSES = ("queued", "running", "waiting_approval", "completed", "failed", "cancelled")
TOOL_STATUSES = (
    "pending",
    "waiting_approval",
    "running",
    "completed",
    "failed",
    "denied",
    "cancelled",
)


def _count_map(rows: list[tuple[str, int]], known_values: tuple[str, ...]) -> dict[str, int]:
    counts = {value: 0 for value in known_values}
    counts.update({status: int(count) for status, count in rows})
    return counts


def _average_ms(value: object) -> int | None:
    return round(float(value)) if value is not None else None


def _failure_alert(
    *,
    code: str,
    title: str,
    failed: int,
    total: int,
    threshold: float,
    min_samples: int,
) -> AdminAlert | None:
    if total < min_samples or total == 0:
        return None
    rate = failed / total
    if rate < threshold:
        return None
    return AdminAlert(
        code=code,
        severity="critical" if rate >= min(threshold * 2, 1) else "warning",
        title=title,
        detail=f"{rate:.1%} ({failed}/{total}), ngưỡng {threshold:.1%}.",
    )


async def get_admin_overview(
    session: AsyncSession,
    *,
    window_days: int = 7,
    now: datetime | None = None,
) -> AdminOverviewResponse:
    if not 1 <= window_days <= 90:
        raise ValueError("window_days phải từ 1 đến 90")
    to_time = now or datetime.now(UTC)
    from_time = to_time - timedelta(days=window_days)

    model_row = (
        await session.execute(
            select(
                func.count(ModelCall.id),
                func.coalesce(func.sum(ModelCall.prompt_tokens), 0),
                func.coalesce(func.sum(ModelCall.completion_tokens), 0),
                func.coalesce(func.sum(ModelCall.total_tokens), 0),
                func.coalesce(func.sum(ModelCall.cached_input_tokens), 0),
                func.coalesce(func.sum(ModelCall.cache_miss_input_tokens), 0),
                func.avg(ModelCall.latency_ms),
            ).where(ModelCall.created_at >= from_time)
        )
    ).one()
    model_calls = int(model_row[0] or 0)
    model_latency_row = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(ModelCall.latency_ms),
                func.percentile_cont(0.95).within_group(ModelCall.latency_ms),
            ).where(
                ModelCall.created_at >= from_time,
                ModelCall.latency_ms.is_not(None),
            )
        )
    ).one()

    model_turns = int(
        await session.scalar(select(func.count(RunStep.id)).where(RunStep.created_at >= from_time))
        or 0
    )
    run_rows = list(
        (
            await session.execute(
                select(AgentRun.status, func.count(AgentRun.id))
                .where(AgentRun.created_at >= from_time)
                .group_by(AgentRun.status)
            )
        ).all()
    )
    run_statuses = _count_map(run_rows, RUN_STATUSES)

    tool_rows = list(
        (
            await session.execute(
                select(ToolCall.status, func.count(ToolCall.id))
                .where(ToolCall.created_at >= from_time)
                .group_by(ToolCall.status)
            )
        ).all()
    )
    tool_statuses = _count_map(tool_rows, TOOL_STATUSES)
    tool_latency_row = (
        await session.execute(
            select(
                func.avg(ToolCall.duration_ms),
                func.percentile_cont(0.5).within_group(ToolCall.duration_ms),
                func.percentile_cont(0.95).within_group(ToolCall.duration_ms),
            ).where(
                ToolCall.created_at >= from_time,
                ToolCall.duration_ms.is_not(None),
            )
        )
    ).one()

    recent_model_calls = list(
        (
            await session.scalars(
                select(ModelCall)
                .where(ModelCall.created_at >= from_time)
                .order_by(ModelCall.created_at.desc())
                .limit(RECENT_ITEM_LIMIT)
            )
        ).all()
    )
    recent_tool_calls = list(
        (
            await session.scalars(
                select(ToolCall)
                .where(ToolCall.created_at >= from_time)
                .order_by(ToolCall.created_at.desc())
                .limit(RECENT_ITEM_LIMIT)
            )
        ).all()
    )

    failed_runs = list(
        (
            await session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.created_at >= from_time,
                    AgentRun.status == "failed",
                )
                .order_by(AgentRun.created_at.desc())
                .limit(RECENT_ITEM_LIMIT)
            )
        ).all()
    )
    failed_model_calls = list(
        (
            await session.scalars(
                select(ModelCall)
                .where(
                    ModelCall.created_at >= from_time,
                    ModelCall.status == "failed",
                )
                .order_by(ModelCall.created_at.desc())
                .limit(RECENT_ITEM_LIMIT)
            )
        ).all()
    )
    failed_tool_calls = list(
        (
            await session.scalars(
                select(ToolCall)
                .where(
                    ToolCall.created_at >= from_time,
                    ToolCall.status == "failed",
                )
                .order_by(ToolCall.created_at.desc())
                .limit(RECENT_ITEM_LIMIT)
            )
        ).all()
    )
    recent_errors = [
        AdminRecentError(
            source="agent_run",
            code=item.stop_reason or "agent_run_failed",
            message=redact_text(
                item.error_message or "Agent run thất bại.",
                max_length=500,
            ),
            created_at=item.created_at,
        )
        for item in failed_runs
    ]
    recent_errors.extend(
        AdminRecentError(
            source="model_call",
            code="model_call_failed",
            message=redact_text(
                item.error_message or "Model call thất bại.",
                max_length=500,
            ),
            created_at=item.created_at,
        )
        for item in failed_model_calls
    )
    recent_errors.extend(
        AdminRecentError(
            source="tool_call",
            code=item.error_code or "tool_call_failed",
            message=redact_text(
                item.error_message or "Tool call thất bại.",
                max_length=500,
            ),
            created_at=item.created_at,
        )
        for item in failed_tool_calls
    )
    recent_errors.sort(key=lambda item: item.created_at, reverse=True)

    failed_run_count = run_statuses["failed"]
    failed_model_count = int(
        await session.scalar(
            select(func.count(ModelCall.id)).where(
                ModelCall.created_at >= from_time,
                ModelCall.status == "failed",
            )
        )
        or 0
    )
    settings = get_settings()
    stuck_run_count = int(
        await session.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.status.in_(("running", "waiting_approval")),
                AgentRun.created_at
                < to_time - timedelta(minutes=settings.admin_alert_stuck_run_minutes),
            )
        )
        or 0
    )
    failed_tool_count = tool_statuses["failed"]
    total_tool_calls = sum(tool_statuses.values())

    prompt_tokens = int(model_row[1] or 0)
    completion_tokens = int(model_row[2] or 0)
    cached_input_tokens = int(model_row[4] or 0)
    cache_miss_input_tokens = int(model_row[5] or 0)
    model_p95_ms = _average_ms(model_latency_row[1])
    cache_hit_rate = calculate_cache_hit_rate(
        cached_input_tokens,
        cache_miss_input_tokens,
    )
    alerts = [
        item
        for item in (
            _failure_alert(
                code="agent_failure_rate",
                title="Tỷ lệ agent run thất bại cao",
                failed=failed_run_count,
                total=sum(run_statuses.values()),
                threshold=settings.admin_alert_failure_rate,
                min_samples=settings.admin_alert_min_sample_count,
            ),
            _failure_alert(
                code="model_failure_rate",
                title="Tỷ lệ model call thất bại cao",
                failed=failed_model_count,
                total=model_calls,
                threshold=settings.admin_alert_failure_rate,
                min_samples=settings.admin_alert_min_sample_count,
            ),
            _failure_alert(
                code="tool_failure_rate",
                title="Tỷ lệ tool call thất bại cao",
                failed=failed_tool_count,
                total=total_tool_calls,
                threshold=settings.admin_alert_failure_rate,
                min_samples=settings.admin_alert_min_sample_count,
            ),
        )
        if item is not None
    ]
    if model_p95_ms is not None and model_p95_ms >= settings.admin_alert_model_p95_ms:
        alerts.append(
            AdminAlert(
                code="model_p95_latency",
                severity="warning",
                title="Model P95 vượt ngưỡng",
                detail=(
                    f"P95 hiện tại {model_p95_ms} ms, "
                    f"ngưỡng {settings.admin_alert_model_p95_ms} ms."
                ),
            )
        )
    cache_sample = cached_input_tokens + cache_miss_input_tokens
    if (
        cache_hit_rate is not None
        and cache_sample >= settings.admin_alert_min_cache_tokens
        and cache_hit_rate < settings.admin_alert_cache_hit_rate
    ):
        alerts.append(
            AdminAlert(
                code="low_cache_hit_rate",
                severity="warning",
                title="Cache hit rate thấp",
                detail=(
                    f"Cache hit hiện tại {cache_hit_rate:.1%}, "
                    f"ngưỡng {settings.admin_alert_cache_hit_rate:.1%}."
                ),
            )
        )
    if stuck_run_count:
        alerts.append(
            AdminAlert(
                code="stuck_agent_runs",
                severity="critical",
                title="Có agent run chạy quá lâu",
                detail=(
                    f"{stuck_run_count} run còn active quá "
                    f"{settings.admin_alert_stuck_run_minutes} phút."
                ),
            )
        )
    return AdminOverviewResponse(
        window_days=window_days,
        from_time=from_time,
        to_time=to_time,
        tokens=AdminTokenMetrics(
            prompt=prompt_tokens,
            completion=completion_tokens,
            total=int(model_row[3] or 0),
            cached_input=cached_input_tokens,
            cache_miss_input=cache_miss_input_tokens,
        ),
        cache=AdminCacheMetrics(
            hit_tokens=cached_input_tokens,
            miss_tokens=cache_miss_input_tokens,
            hit_rate=cache_hit_rate,
        ),
        cost=calculate_token_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_miss_input_tokens=cache_miss_input_tokens,
        ),
        activity=AdminActivityMetrics(
            agent_runs=sum(run_statuses.values()),
            model_turns=model_turns,
            model_calls=model_calls,
            tool_calls=total_tool_calls,
        ),
        latency=AdminLatencyMetrics(
            average_model_ms=_average_ms(model_row[6]),
            model_p50_ms=_average_ms(model_latency_row[0]),
            model_p95_ms=model_p95_ms,
            average_tool_ms=_average_ms(tool_latency_row[0]),
            tool_p50_ms=_average_ms(tool_latency_row[1]),
            tool_p95_ms=_average_ms(tool_latency_row[2]),
        ),
        errors=AdminErrorMetrics(
            total=failed_run_count + failed_model_count + failed_tool_count,
            agent_runs=failed_run_count,
            model_calls=failed_model_count,
            tool_calls=failed_tool_count,
        ),
        tools=AdminToolMetrics(
            completed=tool_statuses["completed"],
            failed=tool_statuses["failed"],
            denied=tool_statuses["denied"],
            waiting_approval=tool_statuses["waiting_approval"],
        ),
        run_statuses=run_statuses,
        recent_model_calls=[
            AdminRecentModelCall(
                model_name=item.model_name,
                status=item.status,
                total_tokens=item.total_tokens or 0,
                latency_ms=item.latency_ms,
                prompt_version=item.prompt_version,
                created_at=item.created_at,
            )
            for item in recent_model_calls
        ],
        recent_tool_calls=[
            AdminRecentToolCall(
                tool_name=item.tool_name,
                status=item.status,
                duration_ms=item.duration_ms,
                error_code=item.error_code,
                created_at=item.created_at,
            )
            for item in recent_tool_calls
        ],
        recent_errors=recent_errors[:RECENT_ITEM_LIMIT],
        alerts=alerts,
    )
