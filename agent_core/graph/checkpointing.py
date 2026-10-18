from __future__ import annotations

from typing import Any
from uuid import UUID


def agent_thread_id(run_id: UUID | str) -> str:
    """Return the stable LangGraph thread id for one agent run."""

    return f"agent-run:{run_id}"


def agent_checkpoint_config(
    run_id: UUID | str,
    *,
    recursion_limit: int | None = None,
) -> dict[str, Any]:
    """Build the config shared by normal execution and resume requests."""

    config: dict[str, Any] = {
        "configurable": {
            "thread_id": agent_thread_id(run_id),
        }
    }
    if recursion_limit is not None:
        config["recursion_limit"] = recursion_limit
    return config
