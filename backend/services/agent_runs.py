import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.graph.checkpointing import agent_checkpoint_config
from agent_core.graph.runtime import AgentLoopEvent
from backend.core.redaction import redact_text
from backend.database import SessionFactory
from backend.models import (
    AgentRun,
    Conversation,
    Message,
    ModelCall,
    RunStep,
    ToolCall,
)


class AgentRunResumeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ToolCallTimeline:
    id: UUID
    tool_name: str
    status: str
    duration_ms: int | None
    error_code: str | None
    approval_decision: str | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunStepTimeline:
    id: UUID
    step_number: int
    attempt_number: int
    status: str
    summary: str | None
    model_name: str | None
    duration_ms: int | None
    total_tokens: int | None
    cached_input_tokens: int
    cache_miss_input_tokens: int
    prompt_cache_hit_rate: float | None
    prompt_version: str | None
    created_at: datetime
    finished_at: datetime | None
    tool_calls: list[ToolCallTimeline]


@dataclass(frozen=True, slots=True)
class AgentRunTimeline:
    id: UUID
    user_message_id: UUID | None
    assistant_message_id: UUID | None
    status: str
    current_step: int
    resume_count: int
    checkpoint_revision: int | None
    stop_reason: str | None
    total_tokens: int
    cached_input_tokens: int
    cache_miss_input_tokens: int
    prompt_cache_hit_rate: float | None
    prompt_version: str | None
    tool_selection_mode: str | None
    available_tool_count: int | None
    loaded_tool_count: int | None
    context_total_tokens: int | None
    context_summary_tokens: int | None
    context_budget_tokens: int | None
    context_trimmed_messages: int
    created_at: datetime
    finished_at: datetime | None
    steps: list[RunStepTimeline]


async def create_agent_run(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    user_message_id: UUID,
) -> AgentRun:
    agent_run = AgentRun(
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        status="running",
    )
    session.add(agent_run)
    await session.commit()
    await session.refresh(agent_run)
    return agent_run


class AgentRunRecorder:
    """Persist AgentLoop events without coupling agent_core to SQLAlchemy."""

    def __init__(
        self,
        *,
        agent_run_id: UUID,
        conversation_id: UUID,
        user_message_id: UUID,
        provider: str,
        model_name: str,
    ) -> None:
        self.agent_run_id = agent_run_id
        self.conversation_id = conversation_id
        self.user_message_id = user_message_id
        self.provider = provider
        self.model_name = model_name
        self._steps: dict[int, tuple[UUID, UUID]] = {}
        self._tool_calls: dict[tuple[int, str], UUID] = {}

    async def __call__(self, event: AgentLoopEvent) -> None:
        handler = getattr(self, f"_record_{event.kind}")
        await handler(event)

    async def _record_model_started(self, event: AgentLoopEvent) -> None:
        step_id = uuid.uuid4()
        model_call_id = uuid.uuid4()
        async with SessionFactory() as session:
            model_call = ModelCall(
                id=model_call_id,
                conversation_id=self.conversation_id,
                user_message_id=self.user_message_id,
                provider=self.provider,
                model_name=self.model_name,
                status="running",
                prompt_version=(
                    event.context_usage.prompt_version if event.context_usage else None
                ),
                prompt_fingerprint=(
                    event.context_usage.prefix_fingerprint if event.context_usage else None
                ),
            )
            run_step = RunStep(
                id=step_id,
                agent_run_id=self.agent_run_id,
                model_call_id=model_call_id,
                step_number=event.step_number,
                status="running",
                summary="Đang yêu cầu model chọn hành động tiếp theo.",
            )
            agent_run = await session.get(AgentRun, self.agent_run_id)
            if agent_run is None:
                raise RuntimeError("Không tìm thấy agent run")
            latest_attempt = await session.scalar(
                select(func.max(RunStep.attempt_number)).where(
                    RunStep.agent_run_id == self.agent_run_id,
                    RunStep.step_number == event.step_number,
                )
            )
            run_step.attempt_number = (latest_attempt or 0) + 1
            agent_run.current_step = event.step_number
            agent_run.status = "running"
            if event.context_usage is not None:
                usage = event.context_usage
                agent_run.context_estimator = usage.estimator
                agent_run.context_system_tokens = usage.system_tokens
                agent_run.context_status_tokens = usage.status_tokens
                agent_run.context_memory_tokens = usage.memory_tokens
                agent_run.context_history_tokens = usage.history_tokens
                agent_run.context_summary_tokens = usage.summary_tokens
                agent_run.context_tool_tokens = usage.tool_tokens
                agent_run.context_rag_tokens = usage.rag_tokens
                agent_run.context_total_tokens = usage.total_tokens
                agent_run.context_budget_tokens = usage.max_input_tokens
                agent_run.context_trimmed_messages += usage.trimmed_messages
                agent_run.tool_selection_mode = usage.tool_selection_mode
                agent_run.available_tool_count = usage.available_tool_count
                agent_run.loaded_tool_count = usage.loaded_tool_count
            session.add(model_call)
            await session.flush()
            session.add(run_step)
            await session.commit()
        self._steps[event.step_number] = (step_id, model_call_id)

    async def _record_model_completed(self, event: AgentLoopEvent) -> None:
        step_id, model_call_id = self._steps[event.step_number]
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            run_step = await session.get(RunStep, step_id)
            model_call = await session.get(ModelCall, model_call_id)
            if run_step is None or model_call is None:
                raise RuntimeError("Không tìm thấy run step hoặc model call")

            run_step.status = "completed"
            run_step.summary = (
                "Model yêu cầu gọi tool."
                if event.finish_reason == "tool_calls"
                else "Model đã tạo câu trả lời."
            )
            run_step.finished_at = now
            model_call.status = "completed"
            model_call.latency_ms = event.duration_ms
            model_call.finished_at = now
            if event.usage is not None:
                model_call.prompt_tokens = event.usage.prompt_tokens
                model_call.completion_tokens = event.usage.completion_tokens
                model_call.total_tokens = event.usage.total_tokens
                model_call.cached_input_tokens = event.usage.cached_input_tokens
                model_call.cache_miss_input_tokens = event.usage.cache_miss_input_tokens
            await session.commit()

    async def _record_model_failed(self, event: AgentLoopEvent) -> None:
        step_id, model_call_id = self._steps[event.step_number]
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            run_step = await session.get(RunStep, step_id)
            model_call = await session.get(ModelCall, model_call_id)
            if run_step is not None:
                run_step.status = "cancelled" if event.finish_reason == "cancelled" else "failed"
                run_step.summary = "Không thể nhận kết quả từ model."
                run_step.finished_at = now
            if model_call is not None:
                model_call.status = "cancelled" if event.finish_reason == "cancelled" else "failed"
                model_call.latency_ms = event.duration_ms
                model_call.error_message = redact_text(
                    event.error_message or "Model call thất bại",
                    max_length=500,
                )
                model_call.finished_at = now
                if event.usage is not None:
                    model_call.prompt_tokens = event.usage.prompt_tokens
                    model_call.completion_tokens = event.usage.completion_tokens
                    model_call.total_tokens = event.usage.total_tokens
                    model_call.cached_input_tokens = event.usage.cached_input_tokens
                    model_call.cache_miss_input_tokens = event.usage.cache_miss_input_tokens
            await session.commit()

    async def _record_tool_started(self, event: AgentLoopEvent) -> None:
        if event.tool_call is None:
            raise RuntimeError("Event tool_started thiếu tool_call")
        step_id, _ = self._steps[event.step_number]
        tool_call_id = uuid.uuid4()
        async with SessionFactory() as session:
            session.add(
                ToolCall(
                    id=tool_call_id,
                    run_step_id=step_id,
                    provider_call_id=event.tool_call.id,
                    tool_name=event.tool_call.name,
                    arguments=dict(event.tool_call.arguments),
                    status="running",
                )
            )
            await session.commit()
        self._tool_calls[(event.step_number, event.tool_call.id)] = tool_call_id

    async def _record_tool_completed(self, event: AgentLoopEvent) -> None:
        if event.tool_call is None or event.tool_result is None:
            raise RuntimeError("Event tool_completed thiếu dữ liệu")
        tool_call_id = self._tool_calls[(event.step_number, event.tool_call.id)]
        async with SessionFactory() as session:
            tool_call = await session.get(ToolCall, tool_call_id)
            if tool_call is None:
                raise RuntimeError("Không tìm thấy tool call")
            if event.tool_result.error_code == "tool_approval_denied":
                tool_call.status = "denied"
            else:
                tool_call.status = "failed" if event.tool_result.is_error else "completed"
            tool_call.result_content = event.tool_result.content
            tool_call.result_data = dict(event.tool_result.data)
            tool_call.error_code = event.tool_result.error_code
            tool_call.error_message = (
                redact_text(event.tool_result.content, max_length=500)
                if event.tool_result.is_error
                else None
            )
            tool_call.duration_ms = event.duration_ms
            tool_call.finished_at = datetime.now(UTC)
            await session.commit()

    async def _record_tool_approval_required(self, event: AgentLoopEvent) -> None:
        if event.tool_call is None or event.approval_id is None:
            raise RuntimeError("Event approval thiếu dữ liệu")
        tool_call_id = self._tool_calls[(event.step_number, event.tool_call.id)]
        async with SessionFactory() as session:
            tool_call = await session.get(ToolCall, tool_call_id)
            agent_run = await session.get(AgentRun, self.agent_run_id)
            if tool_call is None or agent_run is None:
                raise RuntimeError("Không tìm thấy tool call hoặc agent run")
            now = datetime.now(UTC)
            tool_call.status = "waiting_approval"
            tool_call.approval_id = UUID(event.approval_id)
            tool_call.approval_requested_at = now
            agent_run.status = "waiting_approval"
            await session.commit()

    async def _record_tool_approval_resolved(self, event: AgentLoopEvent) -> None:
        if event.tool_call is None or event.approved is None:
            raise RuntimeError("Event approval resolved thiếu dữ liệu")
        tool_call_id = self._tool_calls[(event.step_number, event.tool_call.id)]
        async with SessionFactory() as session:
            tool_call = await session.get(ToolCall, tool_call_id)
            agent_run = await session.get(AgentRun, self.agent_run_id)
            if tool_call is None or agent_run is None:
                raise RuntimeError("Không tìm thấy tool call hoặc agent run")
            tool_call.approval_decision = "approved" if event.approved else "denied"
            tool_call.approval_decided_at = datetime.now(UTC)
            tool_call.status = "running" if event.approved else "denied"
            agent_run.status = "running"
            await session.commit()


async def prepare_agent_run_resume(
    session: AsyncSession,
    *,
    agent_run_id: UUID,
    conversation_id: UUID,
    checkpointer: BaseCheckpointSaver[object],
) -> None:
    agent_run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.id == agent_run_id,
            AgentRun.conversation_id == conversation_id,
        )
        .with_for_update()
    )
    if agent_run is None:
        raise AgentRunResumeError("Không tìm thấy agent run")
    if agent_run.status != "failed":
        raise AgentRunResumeError("Chỉ có thể resume agent run đang ở trạng thái failed")
    checkpoint = await checkpointer.aget_tuple(agent_checkpoint_config(agent_run_id))
    if checkpoint is None:
        raise AgentRunResumeError("Agent run chưa có LangGraph checkpoint để resume")
    channel_values = checkpoint.checkpoint.get("channel_values", {})
    if not isinstance(channel_values, dict) or not channel_values.get("messages"):
        raise AgentRunResumeError("LangGraph checkpoint không hợp lệ hoặc không tương thích")

    now = datetime.now(UTC)
    agent_run.status = "running"
    agent_run.stop_reason = None
    agent_run.error_message = None
    agent_run.finished_at = None
    agent_run.resume_count += 1
    agent_run.last_resumed_at = now
    await session.commit()


async def complete_agent_run(
    *,
    agent_run_id: UUID,
    conversation_id: UUID,
    content: str,
    document_result=None,
    document_owner: UUID | None = None,
) -> Message:
    async with SessionFactory() as session:
        if document_result is not None:
            from backend.services.document_qa import lock_document_answer_scope

            await lock_document_answer_scope(
                session,
                owner=document_owner,
                conversation_id=conversation_id,
                result=document_result,
            )
        assistant_message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
        )
        session.add(assistant_message)
        await session.flush()
        if document_result is not None:
            from backend.services.document_qa import bind_document_citations

            await bind_document_citations(
                session, message=assistant_message, owner=document_owner, result=document_result
            )

        agent_run = await session.get(AgentRun, agent_run_id)
        if agent_run is None:
            raise RuntimeError("Không tìm thấy agent run")
        agent_run.status = "completed"
        if document_result is not None:
            agent_run.rag_trace = document_result.trace
        agent_run.stop_reason = "final_answer"
        agent_run.finished_at = datetime.now(UTC)

        latest_step = await session.scalar(
            select(RunStep)
            .where(RunStep.agent_run_id == agent_run_id)
            .order_by(RunStep.step_number.desc(), RunStep.attempt_number.desc())
            .limit(1)
        )
        if latest_step and latest_step.model_call_id:
            model_call = await session.get(ModelCall, latest_step.model_call_id)
            if model_call:
                model_call.assistant_message_id = assistant_message.id

        conversation = await session.get(Conversation, conversation_id)
        if conversation:
            conversation.updated_at = datetime.now(UTC)

        await session.commit()
        await session.refresh(assistant_message)
        return assistant_message


async def fail_agent_run(
    agent_run_id: UUID,
    status: str,
    message: str,
    *,
    stop_reason: str,
) -> None:
    if status not in {"failed", "cancelled"}:
        raise ValueError("Trạng thái kết thúc agent run không hợp lệ")

    now = datetime.now(UTC)
    async with SessionFactory() as session:
        agent_run = await session.get(AgentRun, agent_run_id)
        if agent_run is None:
            return
        agent_run.status = status
        agent_run.stop_reason = stop_reason
        safe_message = redact_text(message, max_length=500)
        agent_run.error_message = safe_message
        agent_run.finished_at = now

        running_steps = list(
            (
                await session.scalars(
                    select(RunStep).where(
                        RunStep.agent_run_id == agent_run_id,
                        RunStep.status.in_(["pending", "running"]),
                    )
                )
            ).all()
        )
        for run_step in running_steps:
            run_step.status = status
            run_step.finished_at = now
            if run_step.model_call_id:
                model_call = await session.get(ModelCall, run_step.model_call_id)
                if model_call and model_call.status == "running":
                    model_call.status = status
                    model_call.error_message = safe_message
                    model_call.finished_at = now
        step_ids = [run_step.id for run_step in running_steps]
        if step_ids:
            active_tool_calls = list(
                (
                    await session.scalars(
                        select(ToolCall).where(
                            ToolCall.run_step_id.in_(step_ids),
                            ToolCall.status.in_(["pending", "waiting_approval", "running"]),
                        )
                    )
                ).all()
            )
            for tool_call in active_tool_calls:
                tool_call.status = status
                tool_call.error_code = stop_reason
                tool_call.error_message = safe_message
                tool_call.finished_at = now
        await session.commit()


async def list_agent_run_timelines(
    session: AsyncSession,
    conversation_id: UUID,
    *,
    checkpointer: BaseCheckpointSaver[object] | None = None,
) -> list[AgentRunTimeline]:
    runs = list(
        (
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.conversation_id == conversation_id)
                .order_by(AgentRun.created_at, AgentRun.id)
            )
        ).all()
    )
    if not runs:
        return []

    run_ids = [run.id for run in runs]
    steps = list(
        (
            await session.scalars(
                select(RunStep)
                .where(RunStep.agent_run_id.in_(run_ids))
                .order_by(
                    RunStep.agent_run_id,
                    RunStep.step_number,
                    RunStep.attempt_number,
                )
            )
        ).all()
    )
    model_call_ids = [step.model_call_id for step in steps if step.model_call_id is not None]
    model_calls = (
        list(
            (await session.scalars(select(ModelCall).where(ModelCall.id.in_(model_call_ids)))).all()
        )
        if model_call_ids
        else []
    )
    checkpoint_revisions: dict[UUID, int] = {}
    if checkpointer is not None:
        for run in runs:
            if run.status != "failed":
                continue
            checkpoint = await checkpointer.aget_tuple(agent_checkpoint_config(run.id))
            if checkpoint is None:
                continue
            step = checkpoint.metadata.get("step", 0)
            checkpoint_revisions[run.id] = step + 1 if isinstance(step, int) else 1
    step_ids = [step.id for step in steps]
    tool_calls = (
        list(
            (
                await session.scalars(
                    select(ToolCall)
                    .where(ToolCall.run_step_id.in_(step_ids))
                    .order_by(ToolCall.created_at, ToolCall.id)
                )
            ).all()
        )
        if step_ids
        else []
    )

    model_call_by_id = {model_call.id: model_call for model_call in model_calls}
    steps_by_run: dict[UUID, list[RunStep]] = {}
    for step in steps:
        steps_by_run.setdefault(step.agent_run_id, []).append(step)
    tools_by_step: dict[UUID, list[ToolCall]] = {}
    for tool_call in tool_calls:
        tools_by_step.setdefault(tool_call.run_step_id, []).append(tool_call)

    timelines: list[AgentRunTimeline] = []
    for run in runs:
        assistant_message_id: UUID | None = None
        total_tokens = 0
        cached_input_tokens = 0
        cache_miss_input_tokens = 0
        prompt_version: str | None = None
        step_timelines: list[RunStepTimeline] = []
        for step in steps_by_run.get(run.id, []):
            model_call = model_call_by_id.get(step.model_call_id)
            if model_call and model_call.assistant_message_id:
                assistant_message_id = model_call.assistant_message_id
            if model_call and model_call.total_tokens:
                total_tokens += model_call.total_tokens
            step_cache_hits = (model_call.cached_input_tokens or 0) if model_call else 0
            step_cache_misses = (model_call.cache_miss_input_tokens or 0) if model_call else 0
            cached_input_tokens += step_cache_hits
            cache_miss_input_tokens += step_cache_misses
            if model_call and model_call.prompt_version and prompt_version is None:
                prompt_version = model_call.prompt_version
            step_cache_total = step_cache_hits + step_cache_misses
            step_timelines.append(
                RunStepTimeline(
                    id=step.id,
                    step_number=step.step_number,
                    attempt_number=step.attempt_number,
                    status=step.status,
                    summary=step.summary,
                    model_name=model_call.model_name if model_call else None,
                    duration_ms=model_call.latency_ms if model_call else None,
                    total_tokens=model_call.total_tokens if model_call else None,
                    cached_input_tokens=step_cache_hits,
                    cache_miss_input_tokens=step_cache_misses,
                    prompt_cache_hit_rate=(
                        step_cache_hits / step_cache_total if step_cache_total else None
                    ),
                    prompt_version=model_call.prompt_version if model_call else None,
                    created_at=step.created_at,
                    finished_at=step.finished_at,
                    tool_calls=[
                        ToolCallTimeline(
                            id=tool_call.id,
                            tool_name=tool_call.tool_name,
                            status=tool_call.status,
                            duration_ms=tool_call.duration_ms,
                            error_code=tool_call.error_code,
                            approval_decision=tool_call.approval_decision,
                            created_at=tool_call.created_at,
                            finished_at=tool_call.finished_at,
                        )
                        for tool_call in tools_by_step.get(step.id, [])
                    ],
                )
            )
        timelines.append(
            AgentRunTimeline(
                id=run.id,
                user_message_id=run.user_message_id,
                assistant_message_id=assistant_message_id,
                status=run.status,
                current_step=run.current_step,
                resume_count=run.resume_count,
                checkpoint_revision=checkpoint_revisions.get(run.id),
                stop_reason=run.stop_reason,
                total_tokens=total_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_miss_input_tokens=cache_miss_input_tokens,
                prompt_cache_hit_rate=(
                    cached_input_tokens / (cached_input_tokens + cache_miss_input_tokens)
                    if cached_input_tokens + cache_miss_input_tokens
                    else None
                ),
                prompt_version=prompt_version,
                tool_selection_mode=run.tool_selection_mode,
                available_tool_count=run.available_tool_count,
                loaded_tool_count=run.loaded_tool_count,
                context_total_tokens=run.context_total_tokens,
                context_summary_tokens=run.context_summary_tokens,
                context_budget_tokens=run.context_budget_tokens,
                context_trimmed_messages=run.context_trimmed_messages,
                created_at=run.created_at,
                finished_at=run.finished_at,
                steps=step_timelines,
            )
        )
    return timelines
