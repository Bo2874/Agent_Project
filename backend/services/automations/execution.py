"""Trusted execution policies; request/model payloads cannot select a source."""

from agent_core.graph import AgentLimits


def execution_limits_for_source(source, settings):
    if source not in {"chat", "automation"}:
        raise ValueError("Unknown execution source")
    if source == "automation":
        return AgentLimits(max_total_tokens=None)
    return AgentLimits(
        max_steps=settings.agent_max_steps,
        max_total_tokens=settings.agent_run_token_budget,
        max_duration_seconds=settings.agent_max_duration_seconds,
        max_identical_tool_calls=settings.agent_max_identical_tool_calls,
        max_repeated_observations=settings.agent_max_repeated_observations,
    )
