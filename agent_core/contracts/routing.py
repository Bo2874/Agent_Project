from math import isclose
from typing import Annotated, Literal, Self, get_args

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
    tied_routes: tuple[RouteName, ...] = ()
    confidence: Probability | None = None
    provider: Literal["rules", "jev", "llm"]
    model_version: Version | None
    policy_version: Version
    catalog_version: Version
    latency_ms: Milliseconds
    input_tokens: NonNegativeInt | None
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
            maximum = max(self.probabilities.values())
            winners = {route for route, value in self.probabilities.items() if value == maximum}
            if self.tied_routes:
                require_unique(self.tied_routes, "tied_routes")
                # Retain old five-option decisions; current query routing has four options.
                legacy_routes = set(get_args(RouteName))
                query_routes = legacy_routes - {"clarify"}
                four_options = set(self.probabilities) == query_routes
                if (
                    self.route != ("chat" if four_options else "clarify")
                    or self.reason_code != "query_route_tie"
                    or len(winners) < 2
                    or set(self.tied_routes) != winners
                    or set(self.probabilities) not in (query_routes, legacy_routes)
                ):
                    raise ValueError("Tie clarification requires the exact tied maximum routes")
            elif self.probabilities[self.route] < maximum:
                raise ValueError("The selected route must have a maximal probability")
        elif self.tied_routes:
            raise ValueError("Tied routes require a probability distribution")
        elif self.confidence is not None:
            raise ValueError("Confidence requires a probability distribution")
        return self
