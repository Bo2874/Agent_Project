from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import copy_context
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, NoReturn
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.errors import NodeCancelledError, NodeError, NodeTimeoutError
from langgraph.store.base import BaseStore
from langgraph.types import Command, Interrupt, interrupt
from pydantic import ValidationError

from agent_core import planning as dag
from agent_core import task_workers as workers
from agent_core.context_budget import (
    ContextBlock,
    ContextBudgetError,
    ContextTokenBudget,
)
from agent_core.context_middleware import ContextAssemblyMiddleware
from agent_core.contracts import FeatureFlags, RoutingDecision, TaskResult, TaskUsage
from agent_core.graph.budgeting import (
    UNBILLED_TOOL_ERRORS,
    WEB_SEARCH_ESTIMATED_TOKENS,
    final_allowance,
    model_allowance,
    partial_answer,
    tool_allowance,
    unreserved_usage,
)
from agent_core.graph.checkpointing import agent_checkpoint_config, agent_thread_id
from agent_core.graph.limits import AgentGraphLimitError, limit_reason
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
)
from agent_core.graph.state import (
    AgentBudget,
    AgentState,
    create_agent_budget,
    create_initial_agent_state,
)
from agent_core.graph.streaming import (
    AgentGraphStreamEvent,
    map_graph_stream_event,
    write_agent_stream_item,
)
from agent_core.graph.workflow import build_agent_graph
from agent_core.llm.deepseek import (
    LLMConfigurationError,
    LLMProviderError,
    to_langchain_messages,
    to_model_messages,
)
from agent_core.llm.types import ChatModel, LLMChunk, ModelMessage, TokenUsage
from agent_core.routing.query_router import QUERY_POLICY_VERSION
from agent_core.routing.rules import (
    ROUTING_POLICY_VERSION,
    RULES_VERSION,
    clarification_message,
    route_query,
)
from agent_core.routing.shortlist import (
    SHORTLIST_POLICY_VERSION,
    CapabilityChoice,
    SelectionPolicyError,
    ShortlistConfig,
    ShortlistRouter,
)
from agent_core.routing.types import DecisionProvider
from agent_core.skills import (
    SkillRegistry,
    SkillSelectionConfig,
    SkillToolMiddleware,
)
from agent_core.skills.middleware import SkillToolResolution
from agent_core.tools import (
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolSelectionConfig,
)
from agent_core.tools.selector import ToolSelector
from agent_core.tools.types import ToolAuthorizationError
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
    routing_flags: FeatureFlags | None = None,
    decision_provider: DecisionProvider | None = None,
    query_routing_decision: RoutingDecision | None = None,
    routing_messages: Sequence[ModelMessage] | None = None,
    shortlist_config: ShortlistConfig | None = None,
    document_qa_handler: Callable[[], AsyncIterator[AgentStreamItem]] | None = None,
    react_enabled: bool = False,
    usage_reservation=None,
    web_search_estimated_tokens: int = WEB_SEARCH_ESTIMATED_TOKENS,
    planning_enabled: bool = False,
    planning_document_versions: Sequence[str] = (),
    parallel_enabled: bool = False,
    parallel_max_workers: int = 3,
    worker_registry_factory=None,
    worker_capacity=None,
    worker_plan: dict | None = None,
    worker_done=None,
) -> AsyncIterator[AgentStreamItem | RoutingDecision]:
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

    # Existing checkpoints keep their original execution policy. A host flag only
    # chooses the policy for a new run; it cannot silently migrate a paused run.
    if persisted_checkpoint is not None:
        saved_mode = restored.get("execution_mode", "routed")
        if saved_mode not in {"routed", "react"}:
            raise AgentNodeExecutionError(
                "Invalid execution checkpoint; start a new run",
                code="agent_checkpoint_invalid",
            )
        react_enabled = saved_mode == "react"
        planning_enabled = bool(restored.get("planning_enabled", False))
        parallel_enabled = bool(restored.get("parallel_enabled", False))
        parallel_max_workers = int(restored.get("parallel_max_workers", 1))
        if restored.get("planning") and restored["planning"].get("version") != dag.PLANNING_VERSION:
            raise AgentNodeExecutionError(
                "Planning checkpoint version changed", code="planning_checkpoint_changed"
            )
    planning_enabled = planning_enabled and react_enabled
    parallel_enabled = parallel_enabled and planning_enabled and worker_plan is None
    if not 1 <= parallel_max_workers <= 8:
        raise ValueError("Worker concurrency must be between 1 and 8")
    latest_query = _latest_user_query(working_messages)
    if persisted_checkpoint is not None:
        history_source = restored.get("tool_routing_history", [])
    else:
        conversation = list(routing_messages if routing_messages is not None else working_messages)
        last_user = next(
            (
                i
                for i in range(len(conversation) - 1, -1, -1)
                if conversation[i].get("role") == "user"
            ),
            -1,
        )
        history_source = conversation[:last_user] if last_user >= 0 else []
    tool_routing_history = [
        {"role": item["role"], "content": item["content"]}
        for item in history_source
        if isinstance(item, Mapping)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    ]
    routing_decision: RoutingDecision | None = None
    if persisted_checkpoint is not None:
        # Resume the pinned execution policy, including when flags were turned off.
        # Legacy checkpoints remain V1 even if routing was enabled meanwhile.
        saved_route = restored.get("routing_decision")
        if saved_route is not None:
            try:
                routing_decision = RoutingDecision.model_validate(saved_route)
            except ValueError as exc:
                raise AgentNodeExecutionError(
                    "Invalid routing checkpoint; start a new run", code="routing_checkpoint_invalid"
                ) from exc
            valid_policy = (
                routing_decision.provider == "rules"
                and routing_decision.model_version == RULES_VERSION
                and routing_decision.policy_version == ROUTING_POLICY_VERSION
            ) or (
                routing_decision.provider == "jev"
                and routing_decision.policy_version in {QUERY_POLICY_VERSION, "jev_query2.0.0"}
                and decision_provider is not None
            )
            if (
                not valid_policy
                or routing_decision.catalog_version
                != registry.capability_catalog(context.user_role).version
                or routing_decision.route not in {"chat", "tool_task"}
            ):
                raise AgentNodeExecutionError(
                    "Routing capabilities or policy changed; start a new run",
                    code="routing_checkpoint_changed",
                )
    elif query_routing_decision is not None and not react_enabled:
        routing_decision = RoutingDecision.model_validate(query_routing_decision)
        if (
            routing_decision.provider != "jev"
            or routing_decision.policy_version != QUERY_POLICY_VERSION
            or routing_decision.route not in {"chat", "tool_task", "document_qa", "planned_task"}
            or decision_provider is None
            or routing_flags is None
            or not routing_flags.is_enabled("routing")
        ):
            raise AgentNodeExecutionError(
                "Invalid JEV route decision", code="routing_decision_invalid"
            )
        routing_decision = routing_decision.model_copy(
            update={"catalog_version": registry.capability_catalog(context.user_role).version}
        )
    elif not react_enabled and routing_flags is not None and routing_flags.is_enabled("routing"):
        routing_decision = route_query(
            working_messages, registry.capability_catalog(context.user_role)
        )
        if (
            decision_provider is not None
            and not getattr(decision_provider, "shadow", False)
            and skill_registry is not None
            and routing_decision.reason_code == "general_conversation"
            and any(
                match.score >= (skill_selection_config or SkillSelectionConfig()).minimum_score
                for match in skill_registry.search(
                    latest_query,
                    available_tools={
                        r.binding_name
                        for r in registry.capability_catalog(context.user_role).records
                    },
                )
            )
        ):
            routing_decision = routing_decision.model_copy(
                update={"route": "tool_task", "reason_code": "accessible_skill_request"}
            )
    if routing_decision is not None:
        if (
            routing_decision.provider == "rules"
            and decision_provider is not None
            and hasattr(decision_provider, "record_route")
        ):
            try:
                await decision_provider.record_route(
                    routing_decision, resumed=persisted_checkpoint is not None
                )
            except Exception:
                if not getattr(decision_provider, "shadow", False):
                    raise
                logger.warning("Shadow route trace unavailable; baseline execution continues")
        yield routing_decision
        if routing_decision.route == "document_qa" and document_qa_handler is not None:
            async for item in document_qa_handler():
                yield item
            return
        if routing_decision.route in {"document_qa", "planned_task"}:
            message = (
                "Chức năng hỏi đáp theo tài liệu chưa sẵn sàng. "
                "Bạn có thể dán nội dung cần hỏi vào chat."
                if routing_decision.route == "document_qa"
                else "Chức năng điều phối tác vụ nhiều bước chưa sẵn sàng. "
                "Bạn có thể yêu cầu một tác vụ đơn lẻ để tiếp tục."
            )
            yield LLMChunk(content=message, usage=TokenUsage())
            return
        if routing_decision.route == "clarify":
            # A terminal clarification does not call a model, a Skill, or the graph.
            yield LLMChunk(
                content=clarification_message(routing_decision, working_messages),
                usage=TokenUsage(),
            )
            return
    selection_policy = None
    shadow_policy = None
    active_choice = None
    saved_policy = restored.get("capability_routing") if persisted_checkpoint else None
    if saved_policy is not None and saved_policy != SHORTLIST_POLICY_VERSION:
        raise SelectionPolicyError("selection_checkpoint_changed")
    saved_provider_policy = (
        restored.get("decision_policy_version") if persisted_checkpoint else None
    )
    if saved_provider_policy is not None and saved_provider_policy != getattr(
        decision_provider, "execution_signature", None
    ):
        raise SelectionPolicyError("selection_provider_policy_changed")
    if saved_policy is not None or (
        persisted_checkpoint is None
        and not react_enabled
        and decision_provider is not None
        and routing_decision is not None
        and routing_decision.route == "tool_task"
    ):
        if decision_provider is None:
            raise SelectionPolicyError("selection_provider_unavailable")
        if routing_decision is None or routing_decision.route != "tool_task":
            raise SelectionPolicyError("selection_checkpoint_invalid")
        candidate_policy = ShortlistRouter(
            decision_provider,
            registry,
            context,
            skill_registry,
            config=shortlist_config,
            history=tool_routing_history,
            tool_config=replace(
                tool_selection_config or ToolSelectionConfig(),
                schema_token_budget=min(
                    (tool_selection_config or ToolSelectionConfig()).schema_token_budget,
                    context_budget.tool_tokens,
                ),
            ),
        )
        if getattr(decision_provider, "shadow", False) and saved_policy is None:
            shadow_policy = candidate_policy
        else:
            selection_policy = candidate_policy
        if persisted_checkpoint and restored.get("capability_choice") is not None:
            try:
                active_choice = CapabilityChoice.model_validate(restored["capability_choice"])
            except ValueError:
                raise SelectionPolicyError("selection_checkpoint_invalid") from None
    skill_tool_middleware = SkillToolMiddleware(
        tool_registry=registry,
        user_role=context.user_role,
        skill_registry=skill_registry,
        tool_selection_config=replace(
            tool_selection_config or ToolSelectionConfig(),
            schema_token_budget=min(
                (tool_selection_config or ToolSelectionConfig()).schema_token_budget,
                context_budget.tool_tokens,
            ),
        ),
        skill_selection_config=skill_selection_config,
    )
    try:
        if react_enabled:
            # The small-catalog baseline exposes every authorized schema together.
            # Fail closed instead of silently hiding arbitrary tools when it no
            # longer fits. Large-catalog discovery is a separate rollout.
            names = tuple(
                record.binding_name
                for record in registry.capability_catalog(context.user_role).records
            )
            skill_tool_resolution = SkillToolResolution(
                tool_selection=ToolSelector(
                    replace(
                        tool_selection_config or ToolSelectionConfig(),
                        schema_token_budget=min(
                            (tool_selection_config or ToolSelectionConfig()).schema_token_budget,
                            context_budget.tool_tokens,
                        ),
                    )
                ).fit(
                    registry,
                    context.user_role,
                    names,
                    mode="all",
                    required=names,
                    max_tools=len(names),
                ),
                skill_selection=None,
                selected_skill=None,
            )
        elif selection_policy is not None and active_choice is not None:
            skill_tool_resolution = await selection_policy.resolve(active_choice)
        elif selection_policy is not None or (
            routing_decision is not None and routing_decision.route == "chat"
        ):
            # Enforce this in the executor's loaded-name set, not just the prompt.
            skill_tool_resolution = SkillToolResolution(
                tool_selection=ToolSelector().fit(
                    registry, context.user_role, (), mode="all", max_tools=0
                ),
                skill_selection=None,
                selected_skill=None,
            )
        else:
            skill_tool_resolution = skill_tool_middleware.resolve(latest_query)
    except ContextBudgetError as exc:
        raise AgentContextLimitError(str(exc)) from exc
    except ToolAuthorizationError as exc:
        raise AgentNodeExecutionError(str(exc), code=exc.code) from exc
    selection = skill_tool_resolution.tool_selection
    selected_skill = skill_tool_resolution.selected_skill
    if selection_policy is not None and persisted_checkpoint is not None:
        if (
            restored.get("loaded_tool_names") != sorted(selection.loaded_names)
            or restored.get("selected_skill") != (selected_skill.name if selected_skill else None)
            or (
                active_choice is not None
                and active_choice.step_number not in {start_step, max(1, start_step - 1)}
            )
        ):
            raise SelectionPolicyError("selection_checkpoint_invalid")
    original_authorization = context.authorization_guard
    if skill_registry is not None and (selected_skill is not None or selection_policy is not None):

        async def authorize_skill_tool(name: str) -> None:
            if selected_skill is not None:
                await skill_registry.validate(selected_skill.name)
            if original_authorization:
                await original_authorization(name)

        context = replace(context, authorization_guard=authorize_skill_tool)

    tool_definitions = list(selection.definitions)

    def runtime_version():
        if context.capability_version is None and selection_policy is None and not react_enabled:
            return None
        return hashlib.sha256(
            json.dumps(
                {
                    **(
                        {"catalog": registry.capability_catalog(context.user_role).version}
                        if react_enabled
                        else {}
                    ),
                    "authority": context.capability_version,
                    "tools": tool_definitions,
                    "skill": (
                        selected_skill.name,
                        selected_skill.instructions,
                        selected_skill.requires_approval,
                    )
                    if selected_skill
                    else None,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    if planning_enabled:
        if any(name in registry for name in dag.CONTROL_NAMES):
            raise AgentNodeExecutionError(
                "Reserved planning control name collision", code="planning_control_collision"
            )
        tool_definitions = [*tool_definitions, *dag.control_definitions()]
    plugin_runtime_version = runtime_version()
    if persisted_checkpoint is not None and (
        restored.get("plugin_runtime_version") != plugin_runtime_version
    ):
        raise AgentNodeExecutionError(
            "Plugin runtime changed; start a new run", code="plugin_runtime_changed"
        )
    deadline = started_at + limits.max_duration_seconds
    tool_executor = ToolExecutor(
        expected_tools={tool.name: registry.get(tool.name) for tool in selection.tools},
        force_approval=bool(selected_skill and selected_skill.requires_approval),
    )
    context_middleware = ContextAssemblyMiddleware(
        context_budget,
        tool_definitions=tool_definitions,
        memory=memory_context,
        rag=rag_context,
        history_summaries=history_summary_context,
        skill=selected_skill,
        available_tool_count=selection.total_accessible + (2 if planning_enabled else 0),
        tool_selection_mode=selection.mode,
    )
    goal = latest_query.strip() or "Hoàn thành yêu cầu hiện tại."
    if not progress:
        progress.append(
            f"Đã chọn Skill {selected_skill.name} và nhóm tool bắt buộc."
            if selected_skill is not None
            else "Đã tiếp nhận yêu cầu; Agent có thể chọn công cụ theo từng bước."
            if react_enabled
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

    model_started_steps: set[int] = set()
    model_finished_steps: set[int] = set()
    planning_reconciled = False
    last_plan_snapshot = None

    async def publish(item: AgentStreamItem) -> None:
        if isinstance(item, AgentLoopEvent):
            if item.kind == "model_started":
                model_started_steps.add(item.step_number)
            elif item.kind in {"model_completed", "model_failed"}:
                model_finished_steps.add(item.step_number)
            await _emit(event_handler, item)
        write_agent_stream_item(item)

    async def publish_plan(snapshot, step, *, force=False):
        nonlocal last_plan_snapshot
        if not force and snapshot == last_plan_snapshot:
            return
        rows = [("plan.sync", "Kiểm tra kế hoạch", "completed")]
        if snapshot:
            rows = []
            for task in snapshot["spec"]["tasks"]:
                if worker_plan is not None and task["task_id"] != context.task_id:
                    continue
                status = snapshot["statuses"][task["task_id"]]
                display = (
                    "completed"
                    if status == "succeeded"
                    else "failed"
                    if status in {"failed", "blocked", "cancelled"}
                    else "running"
                    if status == "running"
                    else "pending"
                )
                rows.append(
                    (
                        f"plan.task.{task['task_id']}",
                        f"{task['task_id']}: {task['goal'][:140]} · "
                        + {
                            "pending": "Đang chờ",
                            "ready": "Sẵn sàng",
                            "running": "Đang chạy",
                            "succeeded": "Hoàn thành",
                            "failed": "Thất bại",
                            "blocked": "Bị chặn",
                            "cancelled": "Đã dừng",
                        }[status],
                        display,
                    )
                )
        for index, (key, label, status) in enumerate(rows):
            await publish(
                AgentLoopEvent(
                    kind="pipeline_progress",
                    step_number=step,
                    progress={"id": key, "label": label, "state": status, "duration_ms": 0},
                    planning_sync=index == 0,
                    planning_snapshot=snapshot if index == 0 else None,
                )
            )
        last_plan_snapshot = deepcopy(snapshot)

    async def reconcile_planning(state):
        nonlocal planning_reconciled
        if planning_enabled and not planning_reconciled:
            if persisted_checkpoint is not None:
                await publish_plan(state.get("planning"), state["step_number"], force=True)
            planning_reconciled = True

    async def run_worker_waves(state):
        """Fan out private ReAct graphs, then reduce only typed terminal results.

        Step ranges and token caps are leased before launch. Running tasks never
        share messages, ToolExecutor caches, SQL sessions or document contexts.
        """
        plan = deepcopy(state["planning"])
        aggregate = _usage_from_state(state["usage"])
        rows = []
        step = state["step_number"]
        final_reserve = plan.get("final_reserve", 0)
        pool = workers.SharedWorkerBudget(
            state["budget"]["max_total_tokens"]
            - aggregate.total_tokens
            - state.get("budget_held_tokens", 0)
            - final_reserve
        )
        writer = get_stream_writer()
        writer_context = copy_context()
        event_lock = asyncio.Lock()

        @asynccontextmanager
        async def reserve(amount):
            async with pool.reserve(amount, usage_reservation or unreserved_usage) as receipt:
                yield receipt

        @asynccontextmanager
        async def default_registry(task):
            yield registry.scoped(workers.names_for_task(task, registry))

        @asynccontextmanager
        async def capacity_slot():
            if worker_capacity is None:
                yield
            else:
                async with worker_capacity.slot(context.user_id):
                    yield

        while not plan.get("active_task"):
            plan = dag.schedule(plan, {}, 0, activate=False)
            wave = workers.ready_wave(plan, registry, parallel_max_workers)
            if not wave or pool.stopped or deadline <= perf_counter():
                break
            # Re-check leases after actual provider usage, including overruns.
            wave = [
                t
                for t in wave
                if t.budget.max_tokens <= pool.available - pool.spent - pool.held
                and step + t.budget.max_steps <= state["budget"]["max_steps"]
            ]
            if not wave:
                break
            leases = {}
            for task in wave:
                leases[task.task_id] = step
                step += task.budget.max_steps
                plan["statuses"][task.task_id] = "running"
                plan.setdefault("attempts", {})[task.task_id] = {
                    "first_step": leases[task.task_id],
                    "tokens_start": aggregate.total_tokens,
                    "observation_offset": len(state["tool_observations"]) + len(rows),
                    "start_elapsed": _budget_with_elapsed(state, deadline)["elapsed_seconds"],
                }
            await publish_plan(plan, state["step_number"])
            worker_timings = {}

            async def execute_worker(task, leases=leases, plan=plan, timings=worker_timings):
                first = leases[task.task_id]
                captured = None
                known_usage = TokenUsage()

                async def capture(value):
                    nonlocal captured
                    captured = value

                async def record(event):
                    nonlocal known_usage
                    clock = perf_counter() - started_at
                    if event.kind == "model_started":
                        timings.setdefault(
                            task.task_id, {"start_seconds": clock, "end_seconds": clock}
                        )
                    elif event.kind in {"model_completed", "model_failed"}:
                        timings[task.task_id]["end_seconds"] = clock
                    if event.kind in {"model_completed", "model_failed", "tool_completed"}:
                        known_usage += event.usage or TokenUsage()
                    # Child snapshots cannot replace the root DAG projection.
                    progress_row = (
                        {
                            **event.progress,
                            "id": f"worker.{task.task_id}.{event.progress['id']}",
                            "label": f"{task.task_id}: {event.progress.get('label', '')}",
                            "task_id": task.task_id,
                        }
                        if event.progress
                        else None
                    )
                    mapped = replace(
                        event,
                        step_number=first + event.step_number - 1,
                        planning_sync=False,
                        planning_snapshot=None,
                        progress=progress_row,
                    )
                    async with event_lock:
                        await _emit(event_handler, mapped)
                        writer_context.run(
                            writer, AgentGraphStreamEvent(channel="agent_status", item=mapped)
                        )

                failure = None
                worker_started = perf_counter()
                try:
                    async with (
                        asyncio.timeout(
                            max(0.001, min(task.timeout_seconds, deadline - perf_counter()))
                        ),
                        capacity_slot(),
                        (worker_registry_factory or default_registry)(task) as scoped,
                    ):
                        scoped = scoped.scoped(workers.names_for_task(task, scoped))
                        async for _ in run_agent_graph_stream(
                            model=model,
                            messages=[
                                *deepcopy([m for m in messages if m.get("role") == "system"]),
                                {"role": "user", "content": task.goal},
                            ],
                            registry=scoped,
                            context=replace(context, task_id=task.task_id),
                            event_handler=record,
                            limits=AgentLimits(
                                # One host-only closing transition beyond paid task steps.
                                max_steps=task.budget.max_steps + 1,
                                max_total_tokens=task.budget.max_tokens,
                                max_duration_seconds=max(
                                    0.001,
                                    min(
                                        task.timeout_seconds - (perf_counter() - worker_started),
                                        deadline - perf_counter(),
                                    ),
                                ),
                                max_identical_tool_calls=limits.max_identical_tool_calls,
                                max_repeated_observations=limits.max_repeated_observations,
                            ),
                            context_budget=context_budget,
                            rag_context=tuple(
                                b for b in rag_context if b.source == "selected_documents"
                            ),
                            execution_guard=execution_guard,
                            react_enabled=True,
                            planning_enabled=True,
                            usage_reservation=reserve,
                            web_search_estimated_tokens=web_search_estimated_tokens,
                            planning_document_versions=planning_document_versions,
                            worker_plan=workers.worker_seed(plan, task, 0),
                            worker_done=capture,
                        ):
                            # Only the root synthesis is published as assistant prose.
                            pass
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Worker %s did not finish", task.task_id)
                    failure = "worker_failed"
                output = (captured or {}).get("planning", {}).get("results", {}).get(task.task_id)
                if output is None:
                    output = TaskResult(
                        plan_version=plan["spec"]["plan_version"],
                        task_id=task.task_id,
                        attempt_id="attempt1",
                        status="failed",
                        summary="Worker chưa hoàn tất.",
                        error=failure or "worker_incomplete",
                        usage=TaskUsage(total_tokens=known_usage.total_tokens),
                    ).model_dump(mode="json")
                return task, output, captured, known_usage

            # TaskGroup cancels and drains siblings if the parent is cancelled.
            async with asyncio.TaskGroup() as group:
                jobs = [group.create_task(execute_worker(t)) for t in wave]
            for job in jobs:  # Declaration order, never completion-order precedence.
                task, output, captured, known = job.result()
                aggregate += known
                attempt = plan["attempts"][task.task_id]
                attempt["last_step"] = leases[task.task_id] + max(
                    0, min(task.budget.max_steps, output["usage"]["model_steps"]) - 1
                )
                plan = workers.merge_result(plan, output, attempt)
                rows.extend((captured or {}).get("tool_observations", []))
            pairs = list(worker_timings.values())
            plan.setdefault("worker_batches", []).append(
                {
                    "tasks": [t.task_id for t in wave],
                    "overlapped": any(
                        a["start_seconds"] < b["end_seconds"]
                        and b["start_seconds"] < a["end_seconds"]
                        for i, a in enumerate(pairs)
                        for b in pairs[i + 1 :]
                    ),
                    "timings": worker_timings,
                }
            )
            await publish_plan(plan, step)
        return {
            "planning": plan,
            "usage": _usage_to_state(aggregate),
            "step_number": step,
            "budget_held_tokens": state.get("budget_held_tokens", 0) + pool.held,
            "budget_stop_reason": "unknown_billing"
            if pool.held
            else "token_limit"
            if pool.stopped
            else state.get("budget_stop_reason"),
            "tool_observations": rows,
        }

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

    async def validate_capabilities() -> None:
        try:
            if react_enabled and (
                registry.capability_catalog(context.user_role).version != selection.catalog_version
            ):
                raise ToolAuthorizationError("tool_catalog_changed")
            if selection_policy is not None:
                selection_policy.validate_catalog()
            if selected_skill is not None and skill_registry is not None:
                await skill_registry.validate(selected_skill.name)
            if original_authorization:
                for tool in selection.tools:
                    await original_authorization(tool.name)
        except ToolAuthorizationError as exc:
            raise AgentNodeExecutionError(str(exc), code=exc.code) from exc

    def install_resolution(resolution: SkillToolResolution) -> None:
        nonlocal selection, selected_skill, tool_definitions, tool_executor
        nonlocal context_middleware, plugin_runtime_version
        old_version = plugin_runtime_version
        selection, selected_skill = resolution.tool_selection, resolution.selected_skill
        tool_definitions = list(selection.definitions)
        plugin_runtime_version = runtime_version()
        if old_version != plugin_runtime_version:
            tool_executor = ToolExecutor(
                expected_tools={tool.name: registry.get(tool.name) for tool in selection.tools},
                force_approval=bool(selected_skill and selected_skill.requires_approval),
            )
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

    async def restore_node_selection(state: AgentState) -> None:
        nonlocal active_choice
        await reconcile_planning(state)
        if selection_policy is None:
            return
        # LangGraph can apply durable context task writes after a failed checkpoint
        # commit. Runtime closures must follow that effective state, not the older
        # checkpoint channel_values inspected at stream startup.
        try:
            choice = CapabilityChoice.model_validate(state.get("capability_choice"))
        except ValueError:
            raise SelectionPolicyError("selection_checkpoint_invalid") from None
        if choice != active_choice:
            install_resolution(await selection_policy.resolve(choice))
            active_choice = choice
        if (
            state["loaded_tool_names"] != sorted(selection.loaded_names)
            or state["selected_skill"] != (selected_skill.name if selected_skill else None)
            or state["plugin_runtime_version"] != plugin_runtime_version
        ):
            raise SelectionPolicyError("selection_checkpoint_invalid")

    async def prepare_context_node(
        state: AgentState,
        store: BaseStore | None,
    ) -> Mapping[str, Any]:
        nonlocal active_choice
        await reconcile_planning(state)
        if execution_guard is not None and not react_enabled:
            await execution_guard("model")
        await validate_capabilities()
        if shadow_policy is not None:
            # The baseline resolution above remains authoritative. Never resolve a
            # shadow choice: no schema/Skill loading or tool execution from it.
            try:
                shadow_choice = await shadow_policy.choose(
                    latest_query,
                    step_number=state["step_number"],
                    observations=state["tool_observations"],
                )
                if hasattr(decision_provider, "record_choice"):
                    await decision_provider.record_choice(
                        shadow_choice,
                        baseline_tools=selection.loaded_names,
                        baseline_skill=selected_skill.name if selected_skill else None,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Shadow routing unavailable; baseline execution continues")
        if selection_policy is not None:
            # A committed choice is reused on resume/retry. A completed tool batch
            # advances the step and triggers a fresh choice over observation status.
            if active_choice is None or active_choice.step_number != state["step_number"]:
                selection_started = perf_counter()
                selection_progress = {
                    "id": f"tool_selection.{state['step_number']}",
                    "label": "Chọn công cụ phù hợp",
                    "state": "running",
                    "duration_ms": 0,
                    "elapsed_ms": round((selection_started - started_at) * 1000),
                    "started_at": datetime.now(UTC).isoformat(),
                }
                await publish(
                    AgentLoopEvent(
                        kind="pipeline_progress",
                        step_number=state["step_number"],
                        progress=selection_progress,
                    )
                )
                active_choice = await selection_policy.choose(
                    latest_query,
                    step_number=state["step_number"],
                    observations=state["tool_observations"],
                )
                if hasattr(decision_provider, "record_choice"):
                    await decision_provider.record_choice(active_choice)
                try:
                    resolution = await selection_policy.resolve(active_choice)
                except ContextBudgetError as exc:
                    raise AgentContextLimitError(str(exc)) from exc
                install_resolution(resolution)
                await publish(
                    AgentLoopEvent(
                        kind="pipeline_progress",
                        step_number=state["step_number"],
                        progress={
                            **selection_progress,
                            "state": "completed",
                            "duration_ms": round((perf_counter() - selection_started) * 1000),
                            "elapsed_ms": round((perf_counter() - started_at) * 1000),
                        },
                    )
                )
            await validate_capabilities()
        plan = state.get("planning")
        worker_update = {}
        if plan and parallel_enabled and not plan.get("active_task"):
            worker_update = await run_worker_waves(state)
            state = {
                **state,
                **worker_update,
                "tool_observations": [
                    *state["tool_observations"],
                    *worker_update["tool_observations"],
                ],
            }
            plan = state["planning"]
        if plan:
            elapsed = _budget_with_elapsed(state, deadline)["elapsed_seconds"]
            for _ in range(9):
                if worker_plan is not None and not plan.get("active_task"):
                    break
                plan = dag.schedule(plan, state, elapsed)
                remaining = dag.task_remaining(plan, state, elapsed)
                if remaining is None or all(
                    remaining[k] > 0 for k in ("steps", "seconds", "tokens")
                ):
                    break
                plan = dag.close_task(
                    plan,
                    state,
                    summary="Task đã chạm giới hạn thực thi.",
                    error="task_budget",
                )
            state = {**state, "planning": plan}
            await publish_plan(plan, state["step_number"])
        if worker_plan is not None and not plan.get("active_task"):
            result = plan["results"].get(context.task_id)
            return {
                "planning": plan,
                "prepared_messages": state["messages"],
                "direct_answer": True,
                "model_content": result["summary"] if result else "Worker đã dừng.",
            }
        usage = _usage_from_state(state["usage"])
        current_budget = _budget_with_elapsed(state, deadline)
        status_block = _build_status_block(
            goal=state["goal"][:160] if planning_enabled else state["goal"],
            progress=[p[:90] for p in state["progress"][-3:]]
            if planning_enabled
            else state["progress"],
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
            definitions = tool_definitions if planning_enabled else state["tool_definitions"]
            context_messages = state["messages"]
            if plan:
                permitted = dag.allowed_names(plan, registry) | (
                    {"finish_task"} if plan["active_task"] else set()
                )
                definitions = [d for d in tool_definitions if d["function"]["name"] in permitted]
                context_messages = [
                    *context_messages,
                    HumanMessage(content=dag.task_prompt(plan, goal=state["goal"])),
                ]
            elif planning_enabled:
                definitions = [
                    d for d in tool_definitions if d["function"]["name"] != "finish_task"
                ]
            active_rag = _memory_blocks_from_state(state.get("rag_context", []))
            if plan and not (
                dag.allowed_names(plan, registry) & {"search_documents", "summarize_documents"}
            ):
                active_rag = tuple(b for b in active_rag if b.source == "selected_documents")
            prepared = context_middleware.prepare(
                context_messages,
                status=status_block,
                tool_definitions=tool_definitions if selection_policy else definitions,
                memory=active_memory,
                rag=active_rag,
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
        routing_update = {}
        if selection_policy is not None:
            routing_update = {
                "capability_choice": active_choice.model_dump(mode="json"),
                "loaded_tool_names": sorted(selection.loaded_names),
                "selected_skill": selected_skill.name if selected_skill else None,
                "plugin_runtime_version": plugin_runtime_version,
            }
        return {
            **worker_update,
            **routing_update,
            "prepared_messages": prepared.messages,
            "tool_definitions": prepared.tools,
            "budget": _budget_with_elapsed(state, deadline),
            "memory_context": _memory_blocks_to_state(active_memory),
            "memory_loaded": memory_loaded,
            **({"planning": plan} if planning_enabled else {}),
        }

    async def build_partial_node(state: AgentState) -> Mapping[str, Any]:
        await restore_node_selection(state)
        await validate_capabilities()
        if state["step_number"] in model_started_steps - model_finished_steps:
            await publish(
                AgentLoopEvent(
                    kind="model_completed",
                    step_number=state["step_number"],
                    usage=TokenUsage(),
                    finish_reason="budget_partial",
                )
            )
        reason = state.get("budget_stop_reason") or limit_reason(state) or "time_limit"
        plan = state.get("planning")
        if plan:
            if plan.get("active_task"):
                plan = dag.close_task(
                    plan, state, summary="Dừng do giới hạn toàn run.", error=reason
                )
            plan = dag.cancel_pending(plan)
            await publish_plan(plan, state["step_number"])
        content = partial_answer(state, reason)
        await publish(
            AgentLoopEvent(
                kind="pipeline_progress",
                step_number=state["step_number"],
                progress={
                    "id": "budget.partial",
                    "label": "Dừng và lưu kết quả một phần",
                    "state": "completed",
                    "partial": True,
                    "stop_reason": reason,
                    "duration_ms": 0,
                },
            )
        )
        await publish(LLMChunk(content=content, usage=_usage_from_state(state["usage"])))
        return {
            "final_answer": content,
            "model_content": content,
            "pending_tool_calls": [],
            "budget_stop_reason": reason,
            **({"planning": plan} if plan else {}),
        }

    async def budget_progress(step, key, estimate, actual, reason=None):
        await publish(
            AgentLoopEvent(
                kind="pipeline_progress",
                step_number=step,
                progress={
                    "id": f"budget.{key}",
                    "label": "Kiểm tra ngân sách thực thi",
                    "state": "completed",
                    "duration_ms": 0,
                    "estimated_tokens": estimate,
                    "actual_tokens": actual,
                    "billing_status": "known" if actual is not None else "unknown",
                    "stop_reason": reason,
                },
            )
        )

    async def call_model_node(state: AgentState) -> Mapping[str, Any]:
        if not react_enabled:
            return await call_model_unreserved(state)
        output_tokens = getattr(model, "max_tokens", 2_048)
        estimate = model_allowance(
            to_model_messages(state["prepared_messages"]), state["tool_definitions"], output_tokens
        )
        available = (
            state["budget"]["max_total_tokens"]
            - state["usage"]["total_tokens"]
            - state.get("budget_held_tokens", 0)
        )
        remaining = dag.task_remaining(
            state.get("planning"), state, _budget_with_elapsed(state, deadline)["elapsed_seconds"]
        )
        if remaining and (estimate > remaining["tokens"] or remaining["seconds"] <= 0):
            # No paid call: turn the host-validated failure into a task completion.
            request = {
                "id": f"host_budget_{state['step_number']}",
                "name": "finish_task",
                "arguments": {"status": "failed", "summary": "Không đủ ngân sách riêng của task."},
                "raw_arguments": json.dumps(
                    {"status": "failed", "summary": "Không đủ ngân sách riêng của task."}
                ),
                "parse_error_content": None,
                "parse_error_code": None,
            }
            await publish(
                AgentLoopEvent(
                    kind="model_completed",
                    step_number=state["step_number"],
                    usage=TokenUsage(),
                    finish_reason="task_budget",
                )
            )
            return {
                "model_content": "",
                "pending_tool_calls": [request],
                "messages": [
                    AIMessage(
                        content="",
                        additional_kwargs={
                            "tool_calls": [
                                {
                                    "id": request["id"],
                                    "type": "function",
                                    "function": {
                                        "name": "finish_task",
                                        "arguments": request["raw_arguments"],
                                    },
                                }
                            ]
                        },
                    )
                ],
            }
        reason = None
        if estimate > available:
            reason = "token_limit"
        elif deadline <= perf_counter():
            reason = "time_limit"
        if reason is None:
            try:
                if execution_guard is not None:
                    await execution_guard("model")
                async with (usage_reservation or unreserved_usage)(estimate) as receipt:
                    async with asyncio.timeout(
                        max(
                            0.001,
                            min(
                                deadline - perf_counter(),
                                remaining["seconds"] if remaining else float("inf"),
                            ),
                        )
                    ):
                        update = await call_model_unreserved(state, receipt=receipt)
                    await budget_progress(
                        state["step_number"],
                        f"model.{state['step_number']}",
                        estimate,
                        update["usage"]["total_tokens"] - state["usage"]["total_tokens"],
                    )
                    receipt.actual = (
                        update["usage"]["total_tokens"] - state["usage"]["total_tokens"]
                    )
                    return update
            except AgentQuotaLimitError:
                reason = "quota_limit"
            except TimeoutError:
                reason = "time_limit"
                await budget_progress(
                    state["step_number"], f"model.{state['step_number']}", estimate, None, reason
                )
        await publish(
            AgentLoopEvent(
                kind="model_completed",
                step_number=state["step_number"],
                usage=TokenUsage(),
                finish_reason="budget_partial",
            )
        )
        return await build_partial_node({**state, "budget_stop_reason": reason})

    async def call_model_unreserved(state: AgentState, receipt=None) -> Mapping[str, Any]:
        await restore_node_selection(state)
        await validate_capabilities()
        step_number = state["step_number"]
        if step_number not in model_started_steps:
            await publish(AgentLoopEvent(kind="model_started", step_number=step_number))
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
                    if not react_enabled:
                        await publish(LLMChunk(content=chunk.content))
                if chunk.usage is not None:
                    step_usage = chunk.usage
                    if receipt is not None:
                        receipt.known_tokens = max(receipt.known_tokens, step_usage.total_tokens)
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
        if (
            dag.active_spec(state.get("planning"))
            and not call_buffers
            and "".join(content_parts).strip()
        ):
            # A text-only task response is a proposal, not a root final answer.
            call_buffers[0] = _ToolCallBuffer(
                id=f"host_finish_{step_number}",
                name="finish_task",
                argument_parts=[
                    json.dumps(
                        {"status": "succeeded", "summary": "".join(content_parts)},
                        ensure_ascii=False,
                    )
                ],
            )
            finish_reason = "tool_calls"
        await publish(
            AgentLoopEvent(
                kind="model_completed",
                step_number=step_number,
                duration_ms=round((perf_counter() - model_started_at) * 1000),
                usage=step_usage,
                finish_reason=finish_reason,
            )
        )
        if not react_enabled or call_buffers:
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
            if react_enabled:
                # A model may emit prose before a tool call, even in another language.
                # Publish only a confirmed final response; activity stays on the timeline.
                for part in content_parts:
                    await publish(LLMChunk(content=part))
                await publish(LLMChunk(usage=cumulative, finish_reason=finish_reason))
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
            if worker_plan is not None:
                request = replace(request, id=f"{context.task_id}.{step_number}.{request.id}")
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
                    content="" if react_enabled else content,
                    additional_kwargs={"tool_calls": raw_tool_calls},
                )
            ],
            "model_content": "" if react_enabled else content,
            "pending_tool_calls": pending_calls,
            "finish_reason": finish_reason,
            "usage": _usage_to_state(cumulative),
            "budget": _budget_with_elapsed(state, deadline),
            "tool_call_counts": call_counts,
        }

    async def request_approvals_node(state: AgentState) -> Mapping[str, Any]:
        await restore_node_selection(state)
        if selection_policy is not None:
            await validate_capabilities()
        decisions: dict[str, bool] = {}
        if (
            planning_enabled
            and any(c["name"] in dag.CONTROL_NAMES for c in state["pending_tool_calls"])
            and len(state["pending_tool_calls"]) != 1
        ):
            return {"tool_approvals": {}}
        for call in state["pending_tool_calls"]:
            request = _tool_call_request(call)
            if planning_enabled and (
                request.name in dag.CONTROL_NAMES
                or state.get("planning")
                and request.name not in dag.allowed_names(state["planning"], registry)
            ):
                continue
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
        await restore_node_selection(state)
        if selection_policy is not None:
            await validate_capabilities()
        step_number = state["step_number"]
        # Capture before LangChain replaces the tool RunnableConfig context.
        stream_writer = get_stream_writer()
        writer_context = copy_context()

        async def tool_progress(row):
            event = AgentLoopEvent(
                kind="pipeline_progress", step_number=step_number, progress=dict(row)
            )
            await _emit(event_handler, event)
            writer_context.run(
                stream_writer, AgentGraphStreamEvent(channel="agent_status", item=event)
            )

        result_counts = dict(state["observation_counts"])
        tool_messages: list[BaseMessage] = []
        observations: list[dict[str, Any]] = []
        progress_updates: list[str] = []
        tool_usage = TokenUsage()
        internal_model_calls = 0
        document_context = None
        direct_content = None
        budget_stop = None
        plan = state.get("planning")
        mixed_controls = (
            planning_enabled
            and len(state["pending_tool_calls"]) > 1
            and any(c["name"] in dag.CONTROL_NAMES for c in state["pending_tool_calls"])
        )
        final_reserve = (
            final_allowance(
                to_model_messages(state["prepared_messages"]), getattr(model, "max_tokens", 2_048)
            )
            if react_enabled and worker_plan is None
            else 0
        )

        for call in state["pending_tool_calls"]:
            request = _tool_call_request(call)
            control = planning_enabled and request.name in dag.CONTROL_NAMES
            task_limits = dag.task_remaining(
                plan,
                {
                    **state,
                    "usage": _usage_to_state(_usage_from_state(state["usage"]) + tool_usage),
                    "tool_observations": [*state["tool_observations"], *observations],
                },
                _budget_with_elapsed(state, deadline)["elapsed_seconds"],
            )
            task_error = None
            if plan and not control:
                if request.name not in dag.allowed_names(plan, registry):
                    task_error = "task_capability_denied"
                elif task_limits and (
                    task_limits["tools"] <= 0
                    or (web_search_estimated_tokens if request.name == "search_web" else 0)
                    > task_limits["tokens"]
                ):
                    task_error = "task_budget"
            available = (
                state["budget"]["max_total_tokens"]
                - state["usage"]["total_tokens"]
                - tool_usage.total_tokens
                - state.get("budget_held_tokens", 0)
            )
            estimate = (
                tool_allowance(request.name, available, final_reserve, web_search_estimated_tokens)
                if react_enabled
                else 0
            )
            if react_enabled and budget_stop is None:
                if deadline <= perf_counter():
                    budget_stop = "time_limit"
                elif available < estimate + final_reserve:
                    budget_stop = "token_limit"
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
                if (
                    execution_guard is not None
                    and budget_stop is None
                    and not (control or mixed_controls or task_error)
                ):
                    try:
                        await execution_guard("tool")
                    except AgentQuotaLimitError:
                        budget_stop = "quota_limit"
                await publish(
                    AgentLoopEvent(
                        kind="tool_started",
                        step_number=step_number,
                        tool_call=request,
                    )
                )
            elif (
                execution_guard is not None
                and budget_stop is None
                and not (control or mixed_controls or task_error)
            ):
                try:
                    await execution_guard("tool")
                except AgentQuotaLimitError:
                    budget_stop = "quota_limit"

            tool_started_at = perf_counter()
            completion_published = False
            registered = registry.get(request.name) if request.name in registry else None
            if (
                registered
                and registered.tool.return_direct
                and execution_guard is not None
                and not react_enabled
            ):
                await execution_guard("model")

            async def execute(
                request=request,
                parse_error=parse_error,
                approval_granted=approval_granted,
                used=tool_usage.total_tokens,
                child_calls=internal_model_calls,
                task_limits=task_limits,
                plan=plan,
            ):
                return await _execute_tool(
                    tool_executor,
                    registry,
                    replace(
                        context,
                        remaining_tokens=max(
                            0,
                            min(
                                state["budget"]["max_total_tokens"]
                                - state["usage"]["total_tokens"]
                                - used
                                - final_reserve,
                                task_limits["tokens"] if task_limits else float("inf"),
                            ),
                            # Delegated paid models must leave room for the root final turn.
                        ),
                        remaining_steps=max(
                            0,
                            min(
                                state["budget"]["max_steps"] - step_number - child_calls,
                                task_limits["steps"] - 1 if task_limits else 999,
                            ),
                        ),
                        remaining_seconds=max(
                            0,
                            min(
                                deadline - perf_counter(),
                                task_limits["seconds"] if task_limits else float("inf"),
                            ),
                        ),
                        progress_callback=tool_progress,
                    ),
                    request,
                    parse_error,
                    approval_granted,
                    dag.allowed_names(plan, registry)
                    if plan
                    else frozenset(state["loaded_tool_names"]),
                )

            if mixed_controls or task_error:
                result = ToolResult.failure(
                    "Hành động chưa được thực hiện: không phù hợp task hiện tại "
                    "hoặc control phải gọi riêng.",
                    error_code=task_error or "planning_control_mixed",
                )
            elif control:
                try:
                    if parse_error:
                        raise ValueError("Invalid control arguments")
                    if request.name == "create_plan":
                        if plan is not None:
                            raise ValueError("Only one plan is allowed; no nested plan or replan")
                        plan = dag.create_plan(
                            request.arguments,
                            goal=state["goal"],
                            registry=registry,
                            role=context.user_role,
                            remaining_tokens=max(0, available - final_reserve),
                            remaining_steps=state["budget"]["max_steps"] - step_number - 1,
                            remaining_seconds=max(0.001, deadline - perf_counter()),
                            document_versions=planning_document_versions,
                        )
                        plan["final_reserve"] = final_reserve
                        result = ToolResult.success(
                            "Kế hoạch hợp lệ. Host sẽ chọn task sẵn sàng theo dependency."
                        )
                    else:
                        args = dag.FinishRequest.model_validate(request.arguments)
                        plan = dag.close_task(
                            plan,
                            state,
                            summary=args.summary,
                            error=(
                                "task_budget"
                                if request.id.startswith("host_budget_")
                                else "criteria_not_met"
                            )
                            if args.status == "failed"
                            else None,
                        )
                        result = ToolResult.success(
                            "Đã ghi kết quả task; scheduler sẽ chọn task tiếp theo."
                        )
                    await publish_plan(plan, step_number)
                except (ValueError, TypeError, KeyError) as exc:
                    result = ToolResult.failure(
                        "Control chưa hợp lệ: "
                        + (
                            "schema/field validation"
                            if isinstance(exc, ValidationError)
                            else str(exc)[:220]
                        ),
                        error_code="planning_validation",
                    )
            elif budget_stop is not None:
                result = ToolResult.failure(
                    "Chưa thực hiện: không đủ ngân sách cho hành động và câu trả lời cuối.",
                    error_code="agent_budget_skipped",
                )
            else:
                try:
                    async with (
                        (usage_reservation or unreserved_usage)(estimate)
                        if estimate
                        else unreserved_usage(0)
                    ) as receipt:
                        if react_enabled:
                            async with asyncio.timeout(
                                max(
                                    0.001,
                                    min(
                                        deadline - perf_counter(),
                                        task_limits["seconds"] if task_limits else float("inf"),
                                    ),
                                )
                            ):
                                result = await execute()
                        else:
                            result = await execute()
                        if (
                            react_enabled
                            and result.usage is None
                            and result.error_code in UNBILLED_TOOL_ERRORS
                        ):
                            result = replace(result, usage=TokenUsage())
                        # Settle inside the reservation, before the next paid call.
                        await publish(
                            AgentLoopEvent(
                                kind="tool_completed",
                                step_number=step_number,
                                duration_ms=round((perf_counter() - tool_started_at) * 1000),
                                tool_call=request,
                                tool_result=result,
                                usage=result.usage,
                            )
                        )
                        completion_published = True
                        receipt.actual = (
                            result.usage.total_tokens
                            if result.usage is not None
                            else (0 if not estimate else None)
                        )
                except AgentQuotaLimitError:
                    budget_stop = "quota_limit"
                    result = ToolResult.failure(
                        "Chưa thực hiện: không đủ quota dự trù.", error_code="agent_budget_skipped"
                    )
                except TimeoutError:
                    budget_stop = "time_limit"
                    result = ToolResult.failure(
                        "Đã hết thời gian chờ; trạng thái thực thi chưa xác định. "
                        "Không tự động gọi lại.",
                        error_code="tool_outcome_unknown",
                    )
            if not completion_published:
                await publish(
                    AgentLoopEvent(
                        kind="tool_completed",
                        step_number=step_number,
                        duration_ms=round((perf_counter() - tool_started_at) * 1000),
                        tool_call=request,
                        tool_result=result,
                        usage=result.usage,
                    )
                )
            if result.usage is not None:
                tool_usage += result.usage
            elif react_enabled and estimate and result.is_error and budget_stop is None:
                budget_stop = "unknown_billing"
            if react_enabled and estimate:
                await budget_progress(
                    step_number,
                    f"tool.{request.id}",
                    estimate,
                    result.usage.total_tokens
                    if result.usage
                    else 0
                    if result.error_code == "agent_budget_skipped"
                    else None,
                    budget_stop,
                )
            if request.name == "search_documents" and not result.is_error:
                # Checkpoint host evidence in the RAG section rather than repeating
                # its full text in every tool observation. Refined search replaces it.
                document_context = (
                    tuple(
                        block
                        for block in _memory_blocks_from_state(state.get("rag_context", []))
                        if block.source == "selected_documents"
                    )
                    + result.context_blocks
                )
            if request.name == "summarize_documents":
                count = result.data.get("internal_model_calls", 0)
                if type(count) is int and 0 <= count <= state["budget"]["max_steps"]:
                    internal_model_calls += count
            if (
                len(state["pending_tool_calls"]) == 1
                and not react_enabled
                and registered
                and registered.tool.return_direct
            ):
                direct_content = result.content

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

        task_spec = dag.active_spec(plan)
        if (
            task_spec
            and task_spec.agent_type == "web_researcher"
            and len(observations) == 1
            and not observations[0]["is_error"]
            and task_spec.budget.max_tool_calls == 1
            and len(task_spec.allowed_capability_ids) == 1
            and observations[0]["tool_name"] == "search_web"
            and registry.get("search_web").tool.return_direct
        ):
            # Native web already returns a synthesized answer with validated sources.
            # Close this single-action task from that observation; root still synthesizes.
            plan = dag.close_task(
                plan,
                {
                    **state,
                    "tool_observations": [*state["tool_observations"], *observations],
                    "usage": _usage_to_state(_usage_from_state(state["usage"]) + tool_usage),
                },
                summary=observations[0]["content"][:4000],
            )
            await publish_plan(plan, step_number)

        current_budget = _budget_with_elapsed(state, deadline)
        if worker_plan is not None and plan and not plan.get("active_task"):
            direct_content = plan["results"][context.task_id]["summary"]
        return {
            "messages": tool_messages,
            "pending_tool_calls": [],
            "tool_observations": observations,
            "step_number": step_number + 1 + internal_model_calls,
            "progress": progress_updates,
            "observation_counts": result_counts,
            "budget": current_budget,
            "usage": _usage_to_state(_usage_from_state(state["usage"]) + tool_usage),
            "direct_answer": direct_content is not None,
            "budget_stop_reason": budget_stop,
            **({"planning": plan} if planning_enabled else {}),
            **(
                {"rag_context": _memory_blocks_to_state(document_context)}
                if document_context is not None
                else {}
            ),
            **({"model_content": direct_content} if direct_content is not None else {}),
        }

    async def build_final_node(state: AgentState) -> Mapping[str, Any]:
        if (
            worker_plan is None
            and state.get("planning")
            and any(v != "succeeded" for v in state["planning"]["statuses"].values())
        ):
            await publish(
                AgentLoopEvent(
                    kind="pipeline_progress",
                    step_number=state["step_number"],
                    progress={
                        "id": "budget.partial",
                        "label": "Kế hoạch có task chưa thành công",
                        "state": "completed",
                        "partial": True,
                        "stop_reason": "task_failed",
                        "duration_ms": 0,
                    },
                )
            )
        if state.get("direct_answer", False):
            await restore_node_selection(state)
            await validate_capabilities()
            await publish(
                LLMChunk(content=state["model_content"], usage=_usage_from_state(state["usage"]))
            )
        return {"final_answer": state["model_content"]}

    initial_state = create_initial_agent_state(
        to_langchain_messages(working_messages),
        goal=goal,
        budget=initial_budget,
    )
    initial_state.update(
        execution_mode="react" if react_enabled else "routed",
        planning_enabled=planning_enabled,
        parallel_enabled=parallel_enabled,
        parallel_max_workers=parallel_max_workers,
        planning=deepcopy(worker_plan),
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
        tool_routing_history=tool_routing_history,
    )
    graph_context = AgentGraphContext(
        prepare_context=prepare_context_node,
        call_model=call_model_node,
        execute_tools=execute_tools_node,
        build_final=build_final_node,
        request_approvals=request_approvals_node,
        observe_node_error=observe_node_error,
        build_partial=build_partial_node if react_enabled else None,
    )
    initial_state["plugin_runtime_version"] = plugin_runtime_version
    initial_state["routing_decision"] = (
        routing_decision.model_dump(mode="json") if routing_decision is not None else None
    )
    initial_state["capability_routing"] = (
        SHORTLIST_POLICY_VERSION if selection_policy is not None else None
    )
    initial_state["decision_policy_version"] = (
        getattr(decision_provider, "execution_signature", None)
        if selection_policy or (routing_decision and routing_decision.provider == "jev")
        else None
    )
    initial_state["capability_choice"] = (
        active_choice.model_dump(mode="json") if active_choice else None
    )
    graph = build_agent_graph(
        checkpointer=checkpointer,
        store=memory_store,
        # ReAct enforces the shared deadline inside each paid operation; allow
        # cancellation/usage settlement/final persistence to finish afterwards.
        node_timeout_seconds=limits.max_duration_seconds + (10 if react_enabled else 0),
        retry_model=not react_enabled,
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
                model_started_steps.add(resumed_step)
                resumed_model_started = AgentLoopEvent(
                    kind="model_started",
                    step_number=resumed_step,
                )
                await _emit(event_handler, resumed_model_started)
                yield resumed_model_started
            elif pending_nodes.intersection({"approval", "tool"}):
                model_started_steps.add(resumed_step)
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
        if worker_done is not None:
            await worker_done(dict(final_snapshot.values))
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
