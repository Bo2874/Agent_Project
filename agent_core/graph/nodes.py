from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.errors import NodeCancelledError, NodeError, NodeTimeoutError
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import Command

from agent_core.graph.state import AgentNodeName, AgentPhase, AgentState
from agent_core.llm.deepseek import LLMConfigurationError, LLMProviderError

AgentNodeHandler = Callable[[AgentState], Awaitable[Mapping[str, Any]]]
ContextNodeHandler = Callable[
    [AgentState, BaseStore | None],
    Awaitable[Mapping[str, Any]],
]
AgentNodeErrorObserver = Callable[[AgentState, NodeError], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentGraphContext:
    """Run-scoped dependencies; these are never persisted as graph state."""

    prepare_context: ContextNodeHandler
    call_model: AgentNodeHandler
    execute_tools: AgentNodeHandler
    build_final: AgentNodeHandler
    request_approvals: AgentNodeHandler | None = None
    observe_node_error: AgentNodeErrorObserver | None = None
    build_partial: AgentNodeHandler | None = None


_EXPECTED_PHASES: dict[AgentNodeName, frozenset[AgentPhase]] = {
    "context": frozenset({"created", "tools_completed"}),
    "model": frozenset({"context_ready"}),
    "approval": frozenset({"model_completed"}),
    "tool": frozenset({"approval_completed"}),
    "final": frozenset({"model_completed", "tools_completed"}),
}
_OUTPUT_PHASES: dict[AgentNodeName, AgentPhase] = {
    "context": "context_ready",
    "model": "model_completed",
    "approval": "approval_completed",
    "tool": "tools_completed",
    "final": "completed",
}
_RESERVED_UPDATE_KEYS = frozenset({"phase", "node_trace"})


def _get_context(runtime: Runtime[AgentGraphContext]) -> AgentGraphContext:
    if runtime.context is None:
        raise RuntimeError("LangGraph run thiếu AgentGraphContext")
    return runtime.context


async def _run_node(
    name: AgentNodeName,
    state: AgentState,
    handler: AgentNodeHandler,
) -> dict[str, Any]:
    if state["phase"] not in _EXPECTED_PHASES[name]:
        raise RuntimeError(f"Node '{name}' không thể chạy từ phase '{state['phase']}'")
    update = dict(await handler(state))
    reserved = _RESERVED_UPDATE_KEYS.intersection(update)
    if reserved:
        keys = ", ".join(sorted(reserved))
        raise ValueError(f"Node handler không được tự cập nhật lifecycle key: {keys}")
    return {**update, "phase": _OUTPUT_PHASES[name], "node_trace": [name]}


async def context_node(
    state: AgentState,
    runtime: Runtime[AgentGraphContext],
) -> dict[str, Any]:
    context = _get_context(runtime)

    async def prepare_with_store(current_state: AgentState) -> Mapping[str, Any]:
        return await context.prepare_context(current_state, runtime.store)

    update = await _run_node("context", state, prepare_with_store)
    prepared = update.get("prepared_messages")
    if not isinstance(prepared, list) or not prepared:
        raise ValueError("Context node phải tạo prepared_messages")
    return update


async def model_node(
    state: AgentState,
    runtime: Runtime[AgentGraphContext],
) -> dict[str, Any]:
    update = await _run_node("model", state, _get_context(runtime).call_model)
    content = update.get("model_content", "")
    tool_calls = update.get("pending_tool_calls", [])
    if not (isinstance(content, str) and content.strip()) and not tool_calls:
        raise ValueError("Model node phải trả nội dung hoặc tool call")
    return update


async def tool_node(
    state: AgentState,
    runtime: Runtime[AgentGraphContext],
) -> dict[str, Any]:
    return await _run_node("tool", state, _get_context(runtime).execute_tools)


async def approval_node(
    state: AgentState,
    runtime: Runtime[AgentGraphContext],
) -> dict[str, Any]:
    context = _get_context(runtime)
    if context.request_approvals is None:

        async def no_approvals(_: AgentState) -> Mapping[str, Any]:
            return {"tool_approvals": {}}

        handler = no_approvals
    else:
        handler = context.request_approvals
    return await _run_node("approval", state, handler)


async def final_node(
    state: AgentState,
    runtime: Runtime[AgentGraphContext],
) -> dict[str, Any]:
    update = await _run_node("final", state, _get_context(runtime).build_final)
    answer = update.get("final_answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Final node phải tạo final_answer")
    return update


def _node_error_payload(error: NodeError) -> tuple[str, str]:
    exception = error.error
    if isinstance(exception, NodeTimeoutError):
        return "agent_time_limit", "Agent đã vượt quá giới hạn thời gian"
    if isinstance(exception, NodeCancelledError):
        return "agent_cancelled", "Agent đã bị hủy"
    if isinstance(exception, LLMConfigurationError):
        return "missing_api_key", str(exception)
    if isinstance(exception, LLMProviderError):
        return "llm_provider_error", str(exception)

    code = getattr(exception, "code", None)
    if isinstance(code, str) and code:
        return code, str(exception)
    return "agent_node_error", f"Node '{error.node}' thực thi thất bại."


async def node_error_handler(
    state: AgentState,
    error: NodeError,
    runtime: Runtime[AgentGraphContext],
) -> Command[Any]:
    """Convert an exhausted LangGraph node failure into checkpointed state."""

    context = _get_context(runtime)
    if context.observe_node_error is not None:
        await context.observe_node_error(state, error)
    error_code, error_message = _node_error_payload(error)
    trace = [error.node] if error.node in _EXPECTED_PHASES else []
    return Command(
        update={
            "phase": "failed",
            "error_code": error_code,
            "error_message": error_message,
            "error_node": error.node,
            "node_trace": trace,
        },
        goto=END,
    )
