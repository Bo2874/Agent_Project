"""Opt-in decision provider primitives; importing this module never starts a client."""

from agent_core.routing.jev import JevAsyncClient, JevDecisionProvider
from agent_core.routing.types import (
    DecisionOption,
    DecisionProvider,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
)

__all__ = [
    "DecisionOption",
    "DecisionProvider",
    "DecisionProviderError",
    "DecisionRequest",
    "DecisionResult",
    "JevAsyncClient",
    "JevDecisionProvider",
]
