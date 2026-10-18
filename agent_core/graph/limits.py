from __future__ import annotations

from typing import Literal, Never

from agent_core.graph.state import AgentState

AgentLimitReason = Literal[
    "step_limit",
    "token_limit",
    "time_limit",
    "repeated_tool_call",
    "repeated_observation",
]


class AgentGraphLimitError(RuntimeError):
    def __init__(self, reason: AgentLimitReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def limit_reason(state: AgentState) -> AgentLimitReason | None:
    """Return the boundary-specific violation for the state's current phase."""
    budget = state["budget"]
    phase = state["phase"]

    if phase == "context_ready":
        if state["step_number"] > budget["max_steps"]:
            return "step_limit"
        if state["usage"]["total_tokens"] >= budget["max_total_tokens"]:
            return "token_limit"
        if any(
            count > budget["max_identical_tool_calls"]
            for count in state["tool_call_counts"].values()
        ):
            return "repeated_tool_call"
        if any(
            count > budget["max_repeated_observations"]
            for count in state["observation_counts"].values()
        ):
            return "repeated_observation"
        if budget["elapsed_seconds"] >= budget["max_duration_seconds"]:
            return "time_limit"

    if phase == "model_completed" and state["pending_tool_calls"]:
        if state["step_number"] >= budget["max_steps"]:
            return "step_limit"
        if state["usage"]["total_tokens"] >= budget["max_total_tokens"]:
            return "token_limit"
        if budget["elapsed_seconds"] >= budget["max_duration_seconds"]:
            return "time_limit"
        if any(
            count > budget["max_identical_tool_calls"]
            for count in state["tool_call_counts"].values()
        ):
            return "repeated_tool_call"

    if phase == "tools_completed":
        if any(
            count > budget["max_repeated_observations"]
            for count in state["observation_counts"].values()
        ):
            return "repeated_observation"
        if budget["elapsed_seconds"] >= budget["max_duration_seconds"]:
            return "time_limit"

    return None


def route_after_context(state: AgentState) -> Literal["model", "limit"]:
    return "limit" if limit_reason(state) is not None else "model"


def route_after_model(state: AgentState) -> Literal["approval", "final", "limit"]:
    if not state["pending_tool_calls"]:
        return "final"
    return "limit" if limit_reason(state) is not None else "approval"


def route_after_tool(state: AgentState) -> Literal["context", "limit"]:
    return "limit" if limit_reason(state) is not None else "context"


async def limit_node(state: AgentState) -> Never:
    reason = limit_reason(state)
    if reason is None:
        raise RuntimeError("Limit node được gọi khi state không vi phạm giới hạn")

    budget = state["budget"]
    messages = {
        "step_limit": (
            f"Agent đã chạm giới hạn {budget['max_steps']} bước trước khi có câu trả lời cuối"
        ),
        "token_limit": (
            f"Agent đã chạm giới hạn {budget['max_total_tokens']} token "
            "trước khi có câu trả lời cuối"
        ),
        "time_limit": "Agent đã vượt quá giới hạn thời gian",
        "repeated_tool_call": (
            f"Agent lặp lại cùng tool call quá {budget['max_identical_tool_calls']} lần"
        ),
        "repeated_observation": (
            "Agent nhận cùng một observation quá nhiều lần và đã dừng để tránh lặp"
        ),
    }
    raise AgentGraphLimitError(reason, messages[reason])
