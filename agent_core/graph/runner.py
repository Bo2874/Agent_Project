from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from copy import deepcopy
from time import perf_counter
from typing import Any, NoReturn
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeCancelledError, NodeError, NodeTimeoutError
from langgraph.store.base import BaseStore
from langgraph.types import Command, Interrupt, interrupt

from agent_core.context_budget import (
    ContextBlock,
    ContextBudgetError,
    ContextTokenBudget,
)
from agent_core.context_middleware import ContextAssemblyMiddleware
from agent_core.graph.checkpointing import agent_checkpoint_config, agent_thread_id
from agent_core.graph.limits import AgentGraphLimitError
from agent_core.graph.nodes import AgentGraphContext
from agent_core.graph.runtime import (
    AgentContextLimitError,
    AgentEventHandler,
    AgentExecutionGuard,
    AgentLimits,
    AgentLoopCycleError,
    AgentLoopEvent,
    AgentModelStopError,
    AgentNodeExecutionError,
    AgentQuotaLimitError,
    AgentStepLimitError,
    AgentStreamItem,
    AgentTimeLimitError,
    AgentTokenLimitError,
    ToolApprovalHandler,
    ToolCallRequest,
    _build_status_block,
    _emit,
    _execute_tool,
    _fingerprint,
    _latest_user_query,
    _parse_tool_call,
    _raise_for_model_stop,
    _tool_result_message,
    _ToolCallBuffer,
    build_model_tool_definitions,
)
from agent_core.graph.state import (
    AgentBudget,
    AgentState,
    create_agent_budget,
    create_initial_agent_state,
)
from agent_core.graph.streaming import map_graph_stream_event, write_agent_stream_item
from agent_core.graph.workflow import build_agent_graph
from agent_core.llm.deepseek import (
    LLMConfigurationError,
    LLMProviderError,
    to_langchain_messages,
    to_model_messages,
)
from agent_core.llm.types import ChatModel, LLMChunk, ModelMessage, TokenUsage
from agent_core.skills import (
    SkillRegistry,
    SkillSelectionConfig,
    SkillToolMiddleware,
)
from agent_core.tools import (
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolSelectionConfig,
)
from agent_core.tracing import AgentTraceConfig

logger = logging.getLogger(__name__)


def _usage_to_state(usage: TokenUsage) -> dict[str, int]:
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_miss_input_tokens": usage.cache_miss_input_tokens,
    }


def _usage_from_state(usage: Mapping[str, int]) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"],
        cached_input_tokens=usage["cached_input_tokens"],
        cache_miss_input_tokens=usage["cache_miss_input_tokens"],
    )


def _budget_with_elapsed(state: AgentState, deadline: float) -> AgentBudget:
    budget = state["budget"]
    return AgentBudget(
        **{
            **budget,
            "elapsed_seconds": max(
                0,
                budget["max_duration_seconds"] - (deadline - perf_counter()),
            ),
        }
    )


def _limits_from_budget(budget: AgentBudget) -> AgentLimits:
    return AgentLimits(
        max_steps=budget["max_steps"],
        max_total_tokens=budget["max_total_tokens"],
        max_duration_seconds=budget["max_duration_seconds"],
        max_identical_tool_calls=budget["max_identical_tool_calls"],
        max_repeated_observations=budget["max_repeated_observations"],
    )


def _public_limit_error(error: AgentGraphLimitError) -> BaseException:
    if error.reason == "step_limit":
        return AgentStepLimitError(str(error))
    if error.reason == "token_limit":
        return AgentTokenLimitError(str(error))
    if error.reason == "time_limit":
        return AgentTimeLimitError(str(error))
    return AgentLoopCycleError(str(error))


def _raise_graph_node_failure(state: Mapping[str, Any]) -> NoReturn:
    code = state.get("error_code")
    message = state.get("error_message")
    public_message = message if isinstance(message, str) and message else "Agent node thất bại"
    if code == "agent_time_limit":
        raise AgentTimeLimitError(public_message)
    if code == "agent_context_limit":
        raise AgentContextLimitError(public_message)
    if code == "agent_quota_limit":
        raise AgentQuotaLimitError(public_message)
    if code == "missing_api_key":
        raise LLMConfigurationError(public_message)
    if code == "llm_provider_error":
        raise LLMProviderError(public_message)
    if code == "agent_cancelled":
        raise asyncio.CancelledError(public_message)
    if isinstance(code, str) and code.startswith("model_"):
        raise AgentModelStopError(public_message, code=code)
    raise AgentNodeExecutionError(
        "Agent gặp lỗi khi xử lý. Vui lòng thử lại.",
        code=code if isinstance(code, str) and code else "agent_node_error",
    )


def _tool_call_request(call: Mapping[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        id=str(call["id"]),
        name=str(call["name"]),
        arguments=dict(call["arguments"]),
        raw_arguments=str(call["raw_arguments"]),
    )


def _memory_blocks_to_state(blocks: Sequence[ContextBlock]) -> list[dict[str, str | None]]:
    return [{"content": block.content, "source": block.source} for block in blocks]


def _memory_blocks_from_state(value: object) -> tuple[ContextBlock, ...]:
    if not isinstance(value, list):
        return ()
    blocks: list[ContextBlock] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        source = item.get("source")
        if not isinstance(content, str) or not content.strip():
            continue
        blocks.append(
            ContextBlock(
                content=content,
                source=source if isinstance(source, str) else None,
            )
        )
    return tuple(blocks)


def _approval_interrupt_request(value: object) -> tuple[int, ToolCallRequest]:
    if not isinstance(value, dict):
        raise TypeError("LangGraph approval interrupt không đúng định dạng")
    step_number = value.get("step_number")
    tool_call = value.get("tool_call")
    if not isinstance(step_number, int) or not isinstance(tool_call, dict):
        raise TypeError("LangGraph approval interrupt thiếu step hoặc tool_call")
    return step_number, _tool_call_request(tool_call)


async def run_agent_graph_stream(
    *,
    model: ChatModel,
    messages: list[ModelMessage],
    registry: ToolRegistry,
    context: ToolContext,
    event_handler: AgentEventHandler | None = None,
    approval_handler: ToolApprovalHandler | None = None,
    limits: AgentLimits | None = None,
    context_budget: ContextTokenBudget | None = None,
    tool_selection_config: ToolSelectionConfig | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_selection_config: SkillSelectionConfig | None = None,
    memory_context: Sequence[ContextBlock] = (),
    rag_context: Sequence[ContextBlock] = (),
    history_summary_context: Sequence[ContextBlock] = (),
    resume: bool = False,
    execution_guard: AgentExecutionGuard | None = None,
    graph_checkpointer: BaseCheckpointSaver[Any] | None = None,
    memory_store: BaseStore | None = None,
    trace_config: AgentTraceConfig | None = None,
) -> AsyncIterator[AgentStreamItem]:
    """Execute model/tool turns with LangGraph as the only orchestrator."""

    limits = limits or AgentLimits()
    context_budget = context_budget or ContextTokenBudget()
    owns_checkpointer = graph_checkpointer is None
    if resume and owns_checkpointer:
        raise ValueError("Resume yêu cầu LangGraph checkpointer dùng chung")
    checkpointer = graph_checkpointer or InMemorySaver()
    run_id = context.run_id or uuid4()
    thread_id = agent_thread_id(run_id)
    graph_config = agent_checkpoint_config(
        run_id,
        recursion_limit=max(25, limits.max_steps * 4 + 5),
    )
    if trace_config is not None:
        graph_config = trace_config.apply(graph_config)
    persisted_checkpoint = await checkpointer.aget_tuple(graph_config) if resume else None
    if resume and persisted_checkpoint is None:
        raise AgentNodeExecutionError(
            "Không tìm thấy LangGraph checkpoint để resume.",
            code="agent_checkpoint_not_found",
        )

    if persisted_checkpoint is None:
        working_messages = deepcopy(messages)
        cumulative_usage = TokenUsage()
        started_at = perf_counter()
        start_step = 1
        progress: list[str] = []
        tool_call_counts: dict[str, int] = {}
        observation_counts: dict[str, int] = {}
    else:
        restored = persisted_checkpoint.checkpoint.get("channel_values", {})
        if not isinstance(restored, Mapping) or not restored.get("messages"):
            raise AgentNodeExecutionError(
                "LangGraph checkpoint không có AgentState hợp lệ.",
                code="agent_checkpoint_invalid",
            )
        working_messages = to_model_messages(list(restored["messages"]))
        cumulative_usage = _usage_from_state(restored.get("usage", {}))
        restored_budget = restored.get("budget", {})
        elapsed_seconds = (
            float(restored_budget.get("elapsed_seconds", 0))
            if isinstance(restored_budget, Mapping)
            else 0
        )
        started_at = perf_counter() - max(0, elapsed_seconds)
        start_step = int(restored.get("step_number", 1))
        progress = list(restored.get("progress", []))
        memory_context = _memory_blocks_from_state(restored.get("memory_context", []))
        rag_context = _memory_blocks_from_state(restored.get("rag_context", []))
        history_summary_context = _memory_blocks_from_state(
            restored.get("history_summary_context", [])
        )
        tool_call_counts = dict(restored.get("tool_call_counts", {}))
        observation_counts = dict(restored.get("observation_counts", {}))

    latest_query = _latest_user_query(working_messages)
    skill_tool_middleware = SkillToolMiddleware(
        tool_registry=registry,
        user_role=context.user_role,
        skill_registry=skill_registry,
        tool_selection_config=tool_selection_config,
        skill_selection_config=skill_selection_config,
    )
    skill_tool_resolution = skill_tool_middleware.resolve(latest_query)
    selection = skill_tool_resolution.tool_selection
    selected_skill = skill_tool_resolution.selected_skill

    tool_definitions = build_model_tool_definitions(selection.tools)
    deadline = started_at + limits.max_duration_seconds
    tool_executor = ToolExecutor()
    context_middleware = ContextAssemblyMiddleware(
        context_budget,
        tool_definitions=tool_definitions,
        memory=memory_context,
        rag=rag_context,
        history_summaries=history_summary_context,
        skill=selected_skill,
        available_tool_count=selection.total_accessible,
        tool_selection_mode=selection.mode,
    )
    goal = latest_query.strip() or "Hoàn thành yêu cầu hiện tại."
    if not progress:
        progress.append(
            f"Đã chọn Skill {selected_skill.name} và nhóm tool bắt buộc."
            if selected_skill is not None
            else "Đã tiếp nhận yêu cầu và chọn nhóm tool phù hợp."
        )
    initial_budget = create_agent_budget(
        max_steps=limits.max_steps,
        max_total_tokens=limits.max_total_tokens,
        max_duration_seconds=limits.max_duration_seconds,
        elapsed_seconds=max(0, perf_counter() - started_at),
        max_identical_tool_calls=limits.max_identical_tool_calls,
        max_repeated_observations=limits.max_repeated_observations,
    )

    async def publish(item: AgentStreamItem) -> None:
        if isinstance(item, AgentLoopEvent):
            await _emit(event_handler, item)
        write_agent_stream_item(item)

    node_started_at: dict[str, float] = {}

    async def observe_node_error(state: AgentState, error: NodeError) -> None:
        if isinstance(error.error, NodeCancelledError):
            return
        logger.warning(
            "LangGraph node %s failed with %s",
            error.node,
            type(error.error).__name__,
        )
        if error.node != "model":
            return
        started = node_started_at.get("model", started_at)
        message = (
            "Agent đã vượt quá giới hạn thời gian"
            if isinstance(error.error, NodeTimeoutError)
            else str(error.error)
        )
        await publish(
            AgentLoopEvent(
                kind="model_failed",
                step_number=state["step_number"],
                duration_ms=round((perf_counter() - started) * 1000),
                error_message=message,
            )
        )

    async def prepare_context_node(
        state: AgentState,
        store: BaseStore | None,
    ) -> Mapping[str, Any]:
        if execution_guard is not None:
            await execution_guard("model")
        usage = _usage_from_state(state["usage"])
        current_budget = _budget_with_elapsed(state, deadline)
        status_block = _build_status_block(
            goal=state["goal"],
            progress=state["progress"],
            step_number=state["step_number"],
            limits=_limits_from_budget(current_budget),
            used_tokens=usage.total_tokens,
            started_at=started_at,
            elapsed_seconds=current_budget["elapsed_seconds"],
        )
        active_memory = _memory_blocks_from_state(state.get("memory_context", []))
        memory_loaded = bool(state.get("memory_loaded", False))
        if not memory_loaded and store is not None:
            try:
                stored_memories = await store.asearch(
                    ("user_memories", str(context.user_id)),
                    query=_latest_user_query(to_model_messages(state["messages"])),
                    limit=100,
                )
                active_memory = _memory_blocks_from_state(
                    [
                        {
                            "content": item.value.get("content"),
                            "source": item.value.get("source"),
                        }
                        for item in stored_memories
                    ]
                )
            except Exception:
                logger.exception("Không thể truy xuất user memory qua LangGraph Store")
                active_memory = ()
            memory_loaded = True
        try:
            prepared = context_middleware.prepare(
                state["messages"],
                status=status_block,
                tool_definitions=state["tool_definitions"],
                memory=active_memory,
            )
        except ContextBudgetError as exc:
            raise AgentContextLimitError(str(exc)) from exc
        await publish(
            AgentLoopEvent(
                kind="model_started",
                step_number=state["step_number"],
                context_usage=prepared.usage,
                status_block=status_block,
            )
        )
        return {
            "prepared_messages": prepared.messages,
            "tool_definitions": prepared.tools,
            "budget": _budget_with_elapsed(state, deadline),
            "memory_context": _memory_blocks_to_state(active_memory),
            "memory_loaded": memory_loaded,
        }

    async def call_model_node(state: AgentState) -> Mapping[str, Any]:
        step_number = state["step_number"]
        model_started_at = perf_counter()
        node_started_at["model"] = model_started_at
        content_parts: list[str] = []
        call_buffers: dict[int, _ToolCallBuffer] = {}
        step_usage = TokenUsage()
        finish_reason: str | None = None
        try:
            async for chunk in model.stream(
                to_model_messages(state["prepared_messages"]),
                tools=state["tool_definitions"] or None,
                user_id=str(context.user_id),
            ):
                if chunk.content:
                    content_parts.append(chunk.content)
                    await publish(LLMChunk(content=chunk.content))
                if chunk.usage is not None:
                    step_usage = chunk.usage
                if chunk.finish_reason is not None:
                    finish_reason = chunk.finish_reason
                for delta in chunk.tool_call_deltas:
                    buffer = call_buffers.setdefault(delta.index, _ToolCallBuffer())
                    if delta.id is not None:
                        buffer.id = delta.id
                    if delta.name is not None:
                        buffer.name = delta.name
                    if delta.arguments:
                        buffer.argument_parts.append(delta.arguments)
        except LLMProviderError as exc:
            if content_parts or call_buffers:
                raise AgentNodeExecutionError(
                    str(exc),
                    code="llm_provider_error",
                ) from exc
            raise

        cumulative = _usage_from_state(state["usage"]) + step_usage
        await publish(
            AgentLoopEvent(
                kind="model_completed",
                step_number=step_number,
                duration_ms=round((perf_counter() - model_started_at) * 1000),
                usage=step_usage,
                finish_reason=finish_reason,
            )
        )
        await publish(LLMChunk(usage=cumulative, finish_reason=finish_reason))
        _raise_for_model_stop(finish_reason)

        content = "".join(content_parts)
        if not call_buffers:
            if finish_reason == "tool_calls":
                raise AgentModelStopError(
                    "Model báo gọi tool nhưng không cung cấp tool call",
                    code="invalid_model_tool_call",
                )
            if not content.strip():
                raise AgentModelStopError(
                    "Model không trả về nội dung",
                    code="empty_model_response",
                )
            return {
                "messages": [AIMessage(content=content)],
                "model_content": content,
                "pending_tool_calls": [],
                "finish_reason": finish_reason,
                "usage": _usage_to_state(cumulative),
                "budget": _budget_with_elapsed(state, deadline),
            }

        raw_tool_calls: list[dict[str, Any]] = []
        pending_calls: list[dict[str, Any]] = []
        call_counts = dict(state["tool_call_counts"])
        for index, buffer in sorted(call_buffers.items()):
            request, parse_error = _parse_tool_call(
                step_number=step_number,
                index=index,
                buffer=buffer,
            )
            raw_tool_calls.append(
                {
                    "id": request.id,
                    "type": "function",
                    "function": {
                        "name": request.name,
                        "arguments": request.raw_arguments,
                    },
                }
            )
            pending_calls.append(
                {
                    "id": request.id,
                    "name": request.name,
                    "arguments": dict(request.arguments),
                    "raw_arguments": request.raw_arguments,
                    "parse_error_content": parse_error.content if parse_error else None,
                    "parse_error_code": parse_error.error_code if parse_error else None,
                }
            )
            call_signature = _fingerprint(
                {"tool_name": request.name, "arguments": dict(request.arguments)}
            )
            call_counts[call_signature] = call_counts.get(call_signature, 0) + 1
        return {
            "messages": [
                AIMessage(
                    content=content,
                    additional_kwargs={"tool_calls": raw_tool_calls},
                )
            ],
            "model_content": content,
            "pending_tool_calls": pending_calls,
            "finish_reason": finish_reason,
            "usage": _usage_to_state(cumulative),
            "budget": _budget_with_elapsed(state, deadline),
            "tool_call_counts": call_counts,
        }

    async def request_approvals_node(state: AgentState) -> Mapping[str, Any]:
        decisions: dict[str, bool] = {}
        for call in state["pending_tool_calls"]:
            request = _tool_call_request(call)
            if call["parse_error_content"] is not None:
                continue
            if not tool_executor.needs_approval(
                registry,
                context,
                request.name,
                request.arguments,
            ):
                continue
            decision = interrupt(
                {
                    "type": "tool_approval",
                    "step_number": state["step_number"],
                    "tool_call": {
                        "id": request.id,
                        "name": request.name,
                        "arguments": dict(request.arguments),
                        "raw_arguments": request.raw_arguments,
                    },
                }
            )
            decisions[request.id] = decision is True
        return {"tool_approvals": decisions}

    async def execute_tools_node(state: AgentState) -> Mapping[str, Any]:
        step_number = state["step_number"]
        result_counts = dict(state["observation_counts"])
        tool_messages: list[BaseMessage] = []
        observations: list[dict[str, Any]] = []
        progress_updates: list[str] = []

        for call in state["pending_tool_calls"]:
            request = _tool_call_request(call)
            parse_error = (
                ToolResult.failure(
                    call["parse_error_content"],
                    error_code=call["parse_error_code"] or "invalid_tool_arguments",
                )
                if call["parse_error_content"] is not None
                else None
            )
            approval_granted = state["tool_approvals"].get(request.id)
            if request.id not in state["tool_approvals"]:
                if execution_guard is not None:
                    await execution_guard("tool")
                await publish(
                    AgentLoopEvent(
                        kind="tool_started",
                        step_number=step_number,
                        tool_call=request,
                    )
                )
            elif execution_guard is not None:
                await execution_guard("tool")

            tool_started_at = perf_counter()
            result = await _execute_tool(
                tool_executor,
                registry,
                context,
                request,
                parse_error,
                approval_granted,
                frozenset(state["loaded_tool_names"]),
            )

            await publish(
                AgentLoopEvent(
                    kind="tool_completed",
                    step_number=step_number,
                    duration_ms=round((perf_counter() - tool_started_at) * 1000),
                    tool_call=request,
                    tool_result=result,
                )
            )
            tool_messages.extend(to_langchain_messages([_tool_result_message(request, result)]))
            observations.append(
                {
                    "tool_call_id": request.id,
                    "tool_name": request.name,
                    "content": result.content,
                    "data": dict(result.data),
                    "is_error": result.is_error,
                    "error_code": result.error_code,
                }
            )
            observation_signature = _fingerprint(
                {
                    "tool_name": request.name,
                    "content": result.content,
                    "data": dict(result.data),
                    "is_error": result.is_error,
                    "error_code": result.error_code,
                }
            )
            result_counts[observation_signature] = result_counts.get(observation_signature, 0) + 1
            progress_updates.append(
                f"Tool {request.name} "
                f"{'hoàn thành.' if not result.is_error else 'kết thúc với lỗi.'}"
            )

        current_budget = _budget_with_elapsed(state, deadline)
        return {
            "messages": tool_messages,
            "pending_tool_calls": [],
            "tool_observations": observations,
            "step_number": step_number + 1,
            "progress": progress_updates,
            "observation_counts": result_counts,
            "budget": current_budget,
        }

    async def build_final_node(state: AgentState) -> Mapping[str, Any]:
        return {"final_answer": state["model_content"]}

    initial_state = create_initial_agent_state(
        to_langchain_messages(working_messages),
        goal=goal,
        budget=initial_budget,
    )
    initial_state.update(
        step_number=start_step,
        loaded_tool_names=sorted(selection.loaded_names),
        selected_skill=selected_skill.name if selected_skill is not None else None,
        tool_definitions=tool_definitions,
        usage=_usage_to_state(cumulative_usage),
        progress=progress,
        tool_call_counts=tool_call_counts,
        observation_counts=observation_counts,
        memory_context=_memory_blocks_to_state(memory_context),
        memory_loaded=bool(memory_context) or memory_store is None,
        rag_context=_memory_blocks_to_state(rag_context),
        history_summary_context=_memory_blocks_to_state(history_summary_context),
    )
    graph_context = AgentGraphContext(
        prepare_context=prepare_context_node,
        call_model=call_model_node,
        execute_tools=execute_tools_node,
        build_final=build_final_node,
        request_approvals=request_approvals_node,
        observe_node_error=observe_node_error,
    )
    graph = build_agent_graph(
        checkpointer=checkpointer,
        store=memory_store,
        node_timeout_seconds=limits.max_duration_seconds,
    )
    graph_input: AgentState | Command | None = (
        None if persisted_checkpoint is not None else initial_state
    )
    decision_task: asyncio.Task[bool] | None = None

    try:
        if persisted_checkpoint is not None:
            snapshot = await graph.aget_state(graph_config)
            if snapshot.values.get("phase") == "failed":
                failed_node = snapshot.values.get("error_node")
                resume_points = {
                    "context": ("created", "__start__"),
                    "model": ("context_ready", "context"),
                    "approval": ("model_completed", "model"),
                    "tool": ("approval_completed", "approval"),
                    "final": ("model_completed", "model"),
                }
                resume_point = resume_points.get(failed_node)
                if resume_point is not None:
                    resume_phase, as_node = resume_point
                    await graph.aupdate_state(
                        graph_config,
                        {
                            "phase": resume_phase,
                            "error_code": None,
                            "error_message": None,
                            "error_node": None,
                        },
                        as_node=as_node,
                    )
                    snapshot = await graph.aget_state(graph_config)
            pending_nodes = set(snapshot.next)
            resumed_state = snapshot.values
            resumed_step = int(resumed_state.get("step_number", start_step))
            if "model" in pending_nodes:
                resumed_model_started = AgentLoopEvent(
                    kind="model_started",
                    step_number=resumed_step,
                )
                await _emit(event_handler, resumed_model_started)
                yield resumed_model_started
            elif pending_nodes.intersection({"approval", "tool"}):
                resumed_model_started = AgentLoopEvent(
                    kind="model_started",
                    step_number=resumed_step,
                )
                resumed_model_completed = AgentLoopEvent(
                    kind="model_completed",
                    step_number=resumed_step,
                    duration_ms=0,
                    usage=TokenUsage(),
                    finish_reason="tool_calls",
                )
                await _emit(event_handler, resumed_model_started)
                yield resumed_model_started
                await _emit(event_handler, resumed_model_completed)
                yield resumed_model_completed
        while True:
            pending_interrupt: Interrupt | None = None
            pending_step = 0
            pending_request: ToolCallRequest | None = None
            approval_started_at = 0.0

            async for stream_mode, graph_event in graph.astream(
                graph_input,
                context=graph_context,
                config=graph_config,
                stream_mode=["custom", "updates"],
            ):
                if stream_mode == "custom":
                    yield map_graph_stream_event(graph_event)
                    continue
                if not isinstance(graph_event, dict):
                    continue
                interrupts = graph_event.get("__interrupt__", ())
                if not interrupts:
                    continue
                if len(interrupts) != 1 or not isinstance(interrupts[0], Interrupt):
                    raise RuntimeError("Agent chỉ hỗ trợ một approval interrupt tại một thời điểm")

                pending_interrupt = interrupts[0]
                pending_step, pending_request = _approval_interrupt_request(pending_interrupt.value)
                approval_id = pending_interrupt.id
                if approval_handler is not None:
                    decision_task = asyncio.create_task(
                        approval_handler(context, approval_id, pending_request)
                    )
                    await asyncio.sleep(0)
                approval_started_at = perf_counter()
                tool_started = AgentLoopEvent(
                    kind="tool_started",
                    step_number=pending_step,
                    tool_call=pending_request,
                )
                approval_required = AgentLoopEvent(
                    kind="tool_approval_required",
                    step_number=pending_step,
                    tool_call=pending_request,
                    approval_id=approval_id,
                )
                await _emit(event_handler, tool_started)
                yield tool_started
                await _emit(event_handler, approval_required)
                yield approval_required

            if pending_interrupt is None or pending_request is None:
                break

            try:
                approved = await decision_task if decision_task is not None else False
            finally:
                deadline += perf_counter() - approval_started_at
                if decision_task is not None and not decision_task.done():
                    decision_task.cancel()
                if decision_task is not None:
                    await asyncio.gather(decision_task, return_exceptions=True)
                decision_task = None

            approval_resolved = AgentLoopEvent(
                kind="tool_approval_resolved",
                step_number=pending_step,
                tool_call=pending_request,
                approval_id=pending_interrupt.id,
                approved=approved,
            )
            await _emit(event_handler, approval_resolved)
            yield approval_resolved
            graph_input = Command(resume=approved)

        final_snapshot = await graph.aget_state(graph_config)
        if final_snapshot.values.get("phase") == "failed":
            _raise_graph_node_failure(final_snapshot.values)
    except NodeTimeoutError as exc:
        raise AgentTimeLimitError("Agent đã vượt quá giới hạn thời gian") from exc
    except NodeCancelledError as exc:
        raise asyncio.CancelledError("Agent đã bị hủy") from exc
    except AgentGraphLimitError as exc:
        raise _public_limit_error(exc) from exc
    finally:
        if decision_task is not None and not decision_task.done():
            decision_task.cancel()
            await asyncio.gather(decision_task, return_exceptions=True)
        if owns_checkpointer:
            await checkpointer.adelete_thread(thread_id)
