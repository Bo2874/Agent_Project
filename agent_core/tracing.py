from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


@dataclass(frozen=True, slots=True)
class AgentTraceConfig:
    """Safe tracing fields that can be attached to one LangGraph invocation."""

    callbacks: Sequence[BaseCallbackHandler]
    tags: tuple[str, ...]
    metadata: Mapping[str, Any]
    run_name: str = "personal-agent"

    def apply(self, config: Mapping[str, Any]) -> dict[str, Any]:
        traced = dict(config)
        traced["callbacks"] = [*config.get("callbacks", ()), *self.callbacks]
        traced["tags"] = [*config.get("tags", ()), *self.tags]
        traced["metadata"] = {**config.get("metadata", {}), **self.metadata}
        traced["run_name"] = self.run_name
        return traced
