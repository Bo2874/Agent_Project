from typing import Literal, Self

from pydantic import StrictBool, model_validator

from agent_core.contracts.base import ContractModel

FeatureName = Literal["plugins", "routing", "rag", "planning", "parallel", "redis_cache", "events"]
FEATURE_NAMES: tuple[FeatureName, ...] = (
    "plugins",
    "routing",
    "rag",
    "planning",
    "parallel",
    "redis_cache",
    "events",
)


class FeatureFlags(ContractModel):
    """Deployment intent only; availability and permissions remain runtime checks."""

    enabled: StrictBool = False
    plugins: StrictBool = False
    routing: StrictBool = False
    rag: StrictBool = False
    planning: StrictBool = False
    parallel: StrictBool = False
    redis_cache: StrictBool = False
    events: StrictBool = False

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        if self.planning and not self.routing:
            raise ValueError("V2 planning requires routing")
        if self.parallel and not self.planning:
            raise ValueError("V2 parallel execution requires planning")
        return self

    def is_enabled(self, feature: FeatureName) -> bool:
        if feature not in FEATURE_NAMES:
            raise ValueError(f"Unknown V2 feature: {feature}")
        return self.enabled and getattr(self, feature)
