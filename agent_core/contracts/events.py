"""Typed V2 event envelopes, separate from the existing AgentLoopEvent/SSE protocol."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, field_validator, model_validator

from agent_core.contracts.base import (
    ContractModel,
    Identifier,
    Milliseconds,
    PositiveInt,
    Version,
    require_unique,
)
from agent_core.contracts.retrieval import EvidenceReference
from agent_core.contracts.routing import RoutingDecision
from agent_core.contracts.tasks import TaskStatus


class EventEnvelope(ContractModel):
    """Recorder-assigned identity; sequence is monotonically increasing per root run."""

    event_id: UUID
    root_run_id: UUID
    sequence: PositiveInt
    occurred_at: AwareDatetime
    task_id: Identifier | None = None
    plan_version: PositiveInt | None = None
    attempt_id: UUID | None = None

    @field_validator("occurred_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_task_scope(self) -> Self:
        if self.task_id is not None and self.plan_version is None:
            raise ValueError("Task events require plan_version")
        if self.attempt_id is not None and self.task_id is None:
            raise ValueError("attempt_id requires task_id")
        return self


class RoutingDecidedEvent(EventEnvelope):
    kind: Literal["routing_decided"] = "routing_decided"
    decision: RoutingDecision


class RetrievalCompletedEvent(EventEnvelope):
    kind: Literal["retrieval_completed"] = "retrieval_completed"
    evidence_refs: Annotated[tuple[EvidenceReference, ...], Field(max_length=64)] = ()
    retrieval_config_version: Version
    duration_ms: Milliseconds

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        require_unique(tuple(ref.citation_id for ref in self.evidence_refs), "citation_ids")
        return self


class PlanCreatedEvent(EventEnvelope):
    kind: Literal["plan_created"] = "plan_created"
    task_id: None = None
    attempt_id: None = None
    plan_version: PositiveInt
    task_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def validate_task_ids(self) -> Self:
        require_unique(self.task_ids, "task_ids")
        return self


class TaskStatusEvent(EventEnvelope):
    kind: Literal["task_status"] = "task_status"
    task_id: Identifier
    plan_version: PositiveInt
    status: TaskStatus
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status in {"running", "waiting_approval", "succeeded", "failed"}:
            if self.attempt_id is None:
                raise ValueError("Executing/finished task events require attempt_id")
        if self.status in {"failed", "blocked"}:
            if self.error_code is None:
                raise ValueError("Failed/blocked tasks require error_code")
        elif self.error_code is not None:
            raise ValueError("Only failed/blocked tasks carry error_code")
        return self


class RunStatusEvent(EventEnvelope):
    kind: Literal["run_status"] = "run_status"
    task_id: None = None
    attempt_id: None = None
    status: Literal["queued", "running", "waiting_approval", "completed", "failed", "cancelled"]
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("Only failed runs require error_code")
        return self


RunEvent = Annotated[
    RoutingDecidedEvent
    | RetrievalCompletedEvent
    | PlanCreatedEvent
    | TaskStatusEvent
    | RunStatusEvent,
    Field(discriminator="kind"),
]
RUN_EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
