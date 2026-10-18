from typing import Annotated, Literal, Self

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import Field, JsonValue, field_validator, model_validator

from agent_core.contracts.base import (
    ContractModel,
    Identifier,
    NonNegativeInt,
    PositiveInt,
    ShortText,
    SideEffect,
    require_unique,
)
from agent_core.contracts.retrieval import EvidenceReference

WorkerType = Literal["document_researcher", "web_researcher", "data_analyst"]
TaskStatus = Literal[
    "pending",
    "ready",
    "running",
    "waiting_approval",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
]


class TaskBudget(ContractModel):
    """Requested upper bounds; the orchestrator must reserve shared quota separately."""

    max_tokens: PositiveInt
    max_tool_calls: NonNegativeInt
    max_steps: PositiveInt


class TaskInputReference(ContractModel):
    kind: Literal["document_version", "artifact", "task_output"]
    reference_id: ShortText
    source_task_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if (self.kind == "task_output") != (self.source_task_id is not None):
            raise ValueError("Only task_output references require source_task_id")
        return self


class TaskSpec(ContractModel):
    """A proposed task: validation is structural and never authorizes execution."""

    task_id: Identifier
    goal: ShortText
    agent_type: WorkerType
    depends_on: Annotated[tuple[Identifier, ...], Field(max_length=8)] = ()
    input_refs: Annotated[tuple[TaskInputReference, ...], Field(max_length=32)] = ()
    evidence_refs: Annotated[tuple[EvidenceReference, ...], Field(max_length=32)] = ()
    allowed_capability_ids: Annotated[tuple[Identifier, ...], Field(max_length=50)] = ()
    output_schema: dict[str, JsonValue]
    acceptance_criteria: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=16)]
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, le=3600)]
    budget: TaskBudget
    side_effect: SideEffect = "unknown"

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if value.get("type") != "object":
            raise ValueError("Task output_schema must describe a JSON object")
        try:
            validator_for(value).check_schema(value)
        except SchemaError as exc:
            raise ValueError("Task output_schema is not valid JSON Schema") from exc
        return value

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        require_unique(self.depends_on, "depends_on")
        require_unique(self.allowed_capability_ids, "allowed_capability_ids")
        require_unique(
            tuple(r.citation_id for r in self.evidence_refs), "evidence_refs.citation_id"
        )
        if self.task_id in self.depends_on:
            raise ValueError("A task cannot depend on itself")
        for ref in self.input_refs:
            if ref.source_task_id is not None and ref.source_task_id not in self.depends_on:
                raise ValueError("A task_output source must belong to depends_on")
        if self.budget.max_tool_calls and not self.allowed_capability_ids:
            raise ValueError("A non-zero tool budget requires allowed_capability_ids")
        return self
