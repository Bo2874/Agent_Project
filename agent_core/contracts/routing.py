from math import isclose
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from agent_core.contracts.base import (
    ContractModel,
    Identifier,
    Milliseconds,
    NonNegativeInt,
    Probability,
    Version,
    require_unique,
)

RouteName = Literal["chat", "tool_task", "document_qa", "planned_task", "clarify"]
FallbackReason = Literal[
    "low_confidence",
    "ambiguous",
    "provider_error",
    "invalid_response",
    "no_candidate",
    "capability_unavailable",
]


class RoutingDecision(ContractModel):
    """Normalized routing output, not the raw response of any decision provider."""

    route: RouteName
    skill_id: Identifier | None = None
    candidate_tool_ids: Annotated[tuple[Identifier, ...], Field(max_length=64)] = ()
    selected_tool_ids: Annotated[tuple[Identifier, ...], Field(max_length=8)] = ()
    probabilities: dict[RouteName, Probability] = Field(default_factory=dict)
    confidence: Probability | None = None
    provider: Literal["rules", "jev", "llm"]
    model_version: Version
    policy_version: Version
    catalog_version: Version
    latency_ms: Milliseconds
    input_tokens: NonNegativeInt
    reason_code: Identifier
    fallback_reason: FallbackReason | None = None
    cache_hit: StrictBool = False

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        require_unique(self.candidate_tool_ids, "candidate_tool_ids")
        require_unique(self.selected_tool_ids, "selected_tool_ids")
        if not set(self.selected_tool_ids).issubset(self.candidate_tool_ids):
            raise ValueError("Selected tools must belong to candidate_tool_ids")
        if self.route in {"chat", "clarify"} and self.selected_tool_ids:
            raise ValueError("Chat and clarify routes cannot select tools")
        if self.probabilities:
            if self.route not in self.probabilities:
                raise ValueError("The selected route must occur in probabilities")
            if not isclose(sum(self.probabilities.values()), 1, rel_tol=0, abs_tol=1e-6):
                raise ValueError("Route probabilities must sum to one")
            if self.probabilities[self.route] < max(self.probabilities.values()):
                raise ValueError("The selected route must have a maximal probability")
        elif self.confidence is not None:
            raise ValueError("Confidence requires a probability distribution")
        return self
