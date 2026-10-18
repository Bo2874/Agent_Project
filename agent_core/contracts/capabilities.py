from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from agent_core.contracts.base import (
    ContractModel,
    Identifier,
    ShortText,
    SideEffect,
    Version,
    require_unique,
)


class CapabilityDescriptor(ContractModel):
    """Discoverable metadata; a descriptor never grants execution authority."""

    capability_id: Identifier
    kind: Literal["tool", "skill", "retriever", "workflow"]
    name: ShortText
    description: ShortText
    version: Version
    plugin_id: Identifier | None = None
    tags: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()
    required_capability_ids: Annotated[tuple[Identifier, ...], Field(max_length=50)] = ()
    input_schema_ref: Identifier | None = None
    output_schema_ref: Identifier | None = None
    side_effect: SideEffect = "unknown"
    requires_approval: StrictBool = True

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        require_unique(self.tags, "tags")
        require_unique(self.required_capability_ids, "required_capability_ids")
        if self.capability_id in self.required_capability_ids:
            raise ValueError("A capability cannot require itself")
        if self.side_effect != "read_only" and not self.requires_approval:
            raise ValueError("Write, destructive and unknown capabilities require approval")
        return self
