from __future__ import annotations

import operator
from collections.abc import Sequence
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages

AgentNodeName = Literal["context", "model", "approval", "tool", "final"]
AgentPhase = Literal[
    "created",
    "context_ready",
    "model_completed",
    "approval_completed",
    "tools_completed",
    "completed",
    "failed",
]


class AgentToolCall(TypedDict):
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    parse_error_content: str | None
    parse_error_code: str | None


class AgentToolObservation(TypedDict):
    tool_call_id: str
    tool_name: str
    content: str
    data: dict[str, Any]
    is_error: bool
    error_code: str | None


class AgentMemoryBlock(TypedDict):
    content: str
    source: str | None


class AgentTokenUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_input_tokens: int
    cache_miss_input_tokens: int


class AgentBudget(TypedDict):
    max_steps: int
    max_total_tokens: int
    max_duration_seconds: float
    elapsed_seconds: float
    max_identical_tool_calls: int
    max_repeated_observations: int


class AgentState(TypedDict):
    """Serializable state shared by LangGraph agent nodes."""

    messages: Annotated[list[BaseMessage], add_messages]
    prepared_messages: list[BaseMessage]
    goal: str
    phase: AgentPhase
    step_number: int
    loaded_tool_names: list[str]
    selected_skill: str | None
    execution_mode: Literal["routed", "react"]
    plugin_runtime_version: str | None
    routing_decision: dict[str, Any] | None
    capability_routing: str | None
    decision_policy_version: str | None
    capability_choice: dict[str, Any] | None
    tool_routing_history: list[dict[str, str]]
    tool_definitions: list[dict[str, Any]]
    pending_tool_calls: list[AgentToolCall]
    tool_approvals: dict[str, bool]
    tool_observations: Annotated[list[AgentToolObservation], operator.add]
    model_content: str
    direct_answer: bool
    finish_reason: str | None
    usage: AgentTokenUsage
    budget: AgentBudget
    final_answer: str
    progress: Annotated[list[str], operator.add]
    tool_call_counts: dict[str, int]
    observation_counts: dict[str, int]
    memory_context: list[AgentMemoryBlock]
    rag_context: list[AgentMemoryBlock]
    history_summary_context: list[AgentMemoryBlock]
    memory_loaded: bool
    node_trace: Annotated[list[AgentNodeName], operator.add]
    error_code: str | None
    error_message: str | None
    error_node: str | None


def empty_token_usage() -> AgentTokenUsage:
    return AgentTokenUsage(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cached_input_tokens=0,
        cache_miss_input_tokens=0,
    )


def create_agent_budget(
    *,
    max_steps: int = 8,
    max_total_tokens: int = 20_000,
    max_duration_seconds: float = 120,
    elapsed_seconds: float = 0,
    max_identical_tool_calls: int = 3,
    max_repeated_observations: int = 3,
) -> AgentBudget:
    if max_steps < 1:
        raise ValueError("max_steps phải lớn hơn hoặc bằng 1")
    if max_total_tokens < 1:
        raise ValueError("max_total_tokens phải lớn hơn hoặc bằng 1")
    if max_duration_seconds <= 0:
        raise ValueError("max_duration_seconds phải lớn hơn 0")
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds không được âm")
    if max_identical_tool_calls < 1:
        raise ValueError("max_identical_tool_calls phải lớn hơn hoặc bằng 1")
    if max_repeated_observations < 1:
        raise ValueError("max_repeated_observations phải lớn hơn hoặc bằng 1")
    return AgentBudget(
        max_steps=max_steps,
        max_total_tokens=max_total_tokens,
        max_duration_seconds=max_duration_seconds,
        elapsed_seconds=elapsed_seconds,
        max_identical_tool_calls=max_identical_tool_calls,
        max_repeated_observations=max_repeated_observations,
    )


def create_initial_agent_state(
    messages: Sequence[BaseMessage],
    *,
    goal: str,
    budget: AgentBudget | None = None,
) -> AgentState:
    """Create one complete state object instead of relying on missing-key defaults."""
    clean_goal = goal.strip()
    if not clean_goal:
        raise ValueError("Agent goal không được để trống")
    if not messages:
        raise ValueError("AgentState phải có ít nhất một message")
    if not all(isinstance(message, BaseMessage) for message in messages):
        raise TypeError("AgentState messages phải là LangChain BaseMessage")

    return AgentState(
        messages=list(messages),
        prepared_messages=[],
        goal=clean_goal,
        phase="created",
        plugin_runtime_version=None,
        routing_decision=None,
        capability_routing=None,
        decision_policy_version=None,
        capability_choice=None,
        tool_routing_history=[],
        step_number=1,
        loaded_tool_names=[],
        selected_skill=None,
        execution_mode="routed",
        tool_definitions=[],
        pending_tool_calls=[],
        tool_approvals={},
        tool_observations=[],
        model_content="",
        finish_reason=None,
        usage=empty_token_usage(),
        budget=create_agent_budget() if budget is None else dict(budget),
        final_answer="",
        direct_answer=False,
        progress=[],
        tool_call_counts={},
        observation_counts={},
        memory_context=[],
        rag_context=[],
        history_summary_context=[],
        memory_loaded=False,
        node_trace=[],
        error_code=None,
        error_message=None,
        error_node=None,
    )
