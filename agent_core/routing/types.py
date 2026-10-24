"""Small choice boundary; routing policy and provider telemetry are separate concerns."""

from math import isclose
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_core.contracts.base import Identifier, Probability, ShortText, require_unique


class _DecisionModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


class DecisionOption(_DecisionModel):
    """A caller-approved alternative, never execution authority."""

    id: Identifier
    description: ShortText


class DecisionRequest(_DecisionModel):
    """One Choice over text state; the caller owns privacy filtering and available options."""

    state: Annotated[str, Field(strict=True, min_length=1, repr=False)]
    instructions: ShortText
    options: Annotated[tuple[DecisionOption, ...], Field(min_length=2, max_length=64)]
    phase: Literal["capability", "query"] = "capability"

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        require_unique(tuple(option.id for option in self.options), "options.id")
        return self


class DecisionTelemetry(_DecisionModel):
    """Measured provider metadata; absent usage stays unknown, never estimated here."""

    provider: Literal["jev", "llm", "policy"]
    model_version: (
        Annotated[str, Field(max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:+\-]*$")] | None
    ) = None
    input_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    output_tokens: Annotated[int, Field(strict=True, ge=0)] | None = None
    latency_ms: Annotated[float, Field(ge=0)] = 0


class DecisionResult(_DecisionModel):
    """Choice content only; not a complete RoutingDecision or a calibrated correctness score."""

    selected_id: Identifier
    probabilities: Annotated[dict[Identifier, Probability], Field(min_length=2, max_length=64)]
    confidence: Probability | None = None
    telemetry: DecisionTelemetry | None = None

    @model_validator(mode="after")
    def validate_distribution(self) -> Self:
        if self.selected_id not in self.probabilities:
            raise ValueError("Selected option must occur in probabilities")
        if not isclose(sum(self.probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-6):
            raise ValueError("Option probabilities must sum to one")
        if self.probabilities[self.selected_id] < max(self.probabilities.values()):
            raise ValueError("Selected option must have a maximal probability")
        return self


class DecisionProviderError(RuntimeError):
    """Sanitized provider failure; no request, credential or raw response in the message."""

    def __init__(self, code: str) -> None:
        allowed = {
            "provider_error",
            "invalid_response",
            "timeout",
            "circuit_open",
            "privacy_blocked",
            "state_budget",
            "low_confidence",
            "ambiguous",
        }
        if code not in allowed:
            code = "provider_error"
        self.code = code
        super().__init__(
            "Decision provider request failed"
            if code == "provider_error"
            else "Decision provider returned an invalid choice"
        )


class DecisionProvider(Protocol):
    async def decide(self, request: DecisionRequest) -> DecisionResult:
        """Select one of the supplied alternatives without executing it."""
        ...
