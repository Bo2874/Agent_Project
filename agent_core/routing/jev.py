"""Async Choice adapter for a caller-owned TypeSafe client; no SDK import or configuration."""

from collections.abc import Mapping
from time import perf_counter
from typing import Literal, Protocol, TypedDict

from agent_core.routing.types import (
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionTelemetry,
)


class JevChoiceQuestion(TypedDict):
    type: Literal["choice"]
    instructions: str
    criteria: Mapping[str, str | None]


class JevChoiceAnswer(Protocol):
    @property
    def choice(self) -> str: ...

    @property
    def probabilities(self) -> Mapping[str, float]: ...

    @property
    def confidence(self) -> float | None: ...


class JevResponse(Protocol):
    @property
    def choices(self) -> Mapping[str, JevChoiceAnswer]: ...


class JevAsyncClient(Protocol):
    """The public async system_one/choices surface used from AsyncTypeSafeClient."""

    async def system_one(
        self, state: str, questions: Mapping[str, JevChoiceQuestion]
    ) -> JevResponse: ...


class JevDecisionProvider:
    """Borrow an async client. Its construction, settings and cleanup belong to the caller.

    Does not configure auth/model, impose a total deadline, collect usage/version, retry,
    apply routing/fallback policy, or connect to the application's graph.
    """

    def __init__(self, client: JevAsyncClient) -> None:
        self._client = client

    async def decide(self, request: DecisionRequest) -> DecisionResult:
        started = perf_counter()
        request = DecisionRequest.model_validate(request)
        criteria = {option.id: option.description for option in request.options}
        allowed_ids = frozenset(criteria)
        questions: dict[str, JevChoiceQuestion] = {
            "decision": {
                "type": "choice",
                "instructions": request.instructions,
                "criteria": criteria,
            }
        }
        try:
            response = await self._client.system_one(state=request.state, questions=questions)
        except Exception:
            # CancelledError (BaseException) propagates. Do not expose SDK exception bodies.
            raise DecisionProviderError("provider_error") from None

        try:
            choices = response.choices
            if set(choices) != {"decision"}:
                raise ValueError("Unexpected choice questions")
            answer = choices["decision"]
            selected_id = answer.choice
            probabilities = dict(answer.probabilities)
            if selected_id not in allowed_ids or set(probabilities) != allowed_ids:
                raise ValueError("Answer must use the exact supplied option set")
            return DecisionResult(
                selected_id=selected_id,
                probabilities=probabilities,
                confidence=answer.confidence,
                telemetry=DecisionTelemetry(
                    provider="jev",
                    model_version=getattr(response, "model", None),
                    input_tokens=getattr(getattr(response, "usage", None), "input_tokens", None),
                    output_tokens=getattr(getattr(response, "usage", None), "output_tokens", None),
                    latency_ms=(perf_counter() - started) * 1000,
                ),
            )
        except Exception:
            raise DecisionProviderError("invalid_response") from None
