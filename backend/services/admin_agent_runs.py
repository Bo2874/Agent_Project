from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AgentRun, ModelCall, RunStep, ToolCall
from backend.schemas.admin import (
    AdminAgentRunDetail,
    AdminAgentRunModelCall,
    AdminAgentRunsResponse,
    AdminAgentRunStep,
    AdminAgentRunSummary,
    AdminAgentRunToolCall,
)
from backend.services.token_cost import calculate_cache_hit_rate, calculate_token_cost


def _duration_ms(started_at: datetime, finished_at: datetime | None) -> int | None:
    if finished_at is None:
        return None
    return max(round((finished_at - started_at).total_seconds() * 1000), 0)


async def list_admin_agent_runs(
    session: AsyncSession,
    *,
    window_days: int = 7,
    limit: int = 20,
    now: datetime | None = None,
) -> AdminAgentRunsResponse:
    if not 1 <= window_days <= 90:
        raise ValueError("window_days phải từ 1 đến 90")
    if not 1 <= limit <= 100:
        raise ValueError("limit phải từ 1 đến 100")
    from_time = (now or datetime.now(UTC)) - timedelta(days=window_days)
    runs = list(
        (
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.created_at >= from_time)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return AdminAgentRunsResponse(
        runs=[
            AdminAgentRunSummary(
                id=run.id,
                status=run.status,
                current_step=run.current_step,
                stop_reason=run.stop_reason,
                duration_ms=_duration_ms(run.created_at, run.finished_at),
                created_at=run.created_at,
                finished_at=run.finished_at,
            )
            for run in runs
        ]
    )


async def get_admin_agent_run_detail(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> AdminAgentRunDetail | None:
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
    if run is None:
        return None
    steps = list(
        (
            await session.scalars(
                select(RunStep)
                .where(RunStep.agent_run_id == run_id)
                .order_by(RunStep.step_number, RunStep.attempt_number)
            )
        ).all()
    )
    step_ids = [step.id for step in steps]
    model_call_ids = [step.model_call_id for step in steps if step.model_call_id is not None]
    model_calls: list[ModelCall] = []
    tool_calls: list[ToolCall] = []
    if model_call_ids:
        model_calls = list(
            (await session.scalars(select(ModelCall).where(ModelCall.id.in_(model_call_ids)))).all()
        )
    if step_ids:
        tool_calls = list(
            (
                await session.scalars(
                    select(ToolCall)
                    .where(ToolCall.run_step_id.in_(step_ids))
                    .order_by(ToolCall.created_at, ToolCall.id)
                )
            ).all()
        )

    models_by_id = {item.id: item for item in model_calls}
    tools_by_step: dict[UUID, list[ToolCall]] = {}
    for tool_call in tool_calls:
        tools_by_step.setdefault(tool_call.run_step_id, []).append(tool_call)

    prompt_tokens = sum(item.prompt_tokens or 0 for item in model_calls)
    completion_tokens = sum(item.completion_tokens or 0 for item in model_calls)
    cached_tokens = sum(item.cached_input_tokens or 0 for item in model_calls)
    cache_miss_tokens = sum(item.cache_miss_input_tokens or 0 for item in model_calls)
    total_cost = calculate_token_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_input_tokens=cached_tokens,
        cache_miss_input_tokens=cache_miss_tokens,
    )

    step_details: list[AdminAgentRunStep] = []
    for step in steps:
        model_call = models_by_id.get(step.model_call_id) if step.model_call_id else None
        model_detail = None
        if model_call is not None:
            call_cost = calculate_token_cost(
                prompt_tokens=model_call.prompt_tokens or 0,
                completion_tokens=model_call.completion_tokens or 0,
                cached_input_tokens=model_call.cached_input_tokens or 0,
                cache_miss_input_tokens=model_call.cache_miss_input_tokens or 0,
            )
            model_detail = AdminAgentRunModelCall(
                id=model_call.id,
                model_name=model_call.model_name,
                status=model_call.status,
                prompt_version=model_call.prompt_version,
                prompt_tokens=model_call.prompt_tokens or 0,
                completion_tokens=model_call.completion_tokens or 0,
                total_tokens=model_call.total_tokens or 0,
                cached_input_tokens=model_call.cached_input_tokens or 0,
                cache_miss_input_tokens=model_call.cache_miss_input_tokens or 0,
                cache_hit_rate=calculate_cache_hit_rate(
                    model_call.cached_input_tokens or 0,
                    model_call.cache_miss_input_tokens or 0,
                ),
                estimated_cost_usd=call_cost.estimated_total,
                latency_ms=model_call.latency_ms,
            )
        step_details.append(
            AdminAgentRunStep(
                id=step.id,
                step_number=step.step_number,
                attempt_number=step.attempt_number,
                status=step.status,
                duration_ms=_duration_ms(step.created_at, step.finished_at),
                model_call=model_detail,
                tool_calls=[
                    AdminAgentRunToolCall(
                        id=tool.id,
                        tool_name=tool.tool_name,
                        status=tool.status,
                        duration_ms=tool.duration_ms,
                        error_code=tool.error_code,
                        approval_decision=tool.approval_decision,
                    )
                    for tool in tools_by_step.get(step.id, [])
                ],
            )
        )

    return AdminAgentRunDetail(
        id=run.id,
        conversation_id=run.conversation_id,
        status=run.status,
        current_step=run.current_step,
        stop_reason=run.stop_reason,
        duration_ms=_duration_ms(run.created_at, run.finished_at),
        resume_count=run.resume_count,
        context_total_tokens=run.context_total_tokens,
        context_budget_tokens=run.context_budget_tokens,
        context_trimmed_messages=run.context_trimmed_messages,
        tool_selection_mode=run.tool_selection_mode,
        available_tool_count=run.available_tool_count,
        loaded_tool_count=run.loaded_tool_count,
        total_tokens=sum(item.total_tokens or 0 for item in model_calls),
        cache_hit_rate=calculate_cache_hit_rate(cached_tokens, cache_miss_tokens),
        estimated_cost_usd=total_cost.estimated_total,
        created_at=run.created_at,
        finished_at=run.finished_at,
        steps=step_details,
    )
