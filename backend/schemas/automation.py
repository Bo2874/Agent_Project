from datetime import datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.google import GoogleFileId


class AutomationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ScheduleSpec(AutomationModel):
    kind: Literal["once", "daily", "weekly"]
    timezone: str = Field(max_length=100)
    local_time: time | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    at: datetime | None = None

    @model_validator(mode="after")
    def valid_schedule(self):
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        if self.kind == "once":
            if (
                self.at is None
                or self.at.utcoffset() is None
                or self.local_time is not None
                or self.weekday is not None
            ):
                raise ValueError("One-time schedule requires only an aware timestamp")
        elif (
            self.at is not None
            or self.local_time is None
            or self.local_time.tzinfo is not None
            or (self.weekday is not None) != (self.kind == "weekly")
        ):
            raise ValueError("Recurring schedule requires local time and weekly weekday")
        if self.local_time and (self.local_time.second or self.local_time.microsecond):
            raise ValueError("Schedule time uses minute precision")
        return self


GoogleAction = Literal[
    "read_sheet",
    "append_rows",
    "update_range",
    "set_formulas",
    "format_range",
    "read_doc",
    "edit_doc",
    "create_doc",
    "upload_artifact",
]


class GrantDraft(AutomationModel):
    connection_id: UUID
    file_id: GoogleFileId | None = None
    action: GoogleAction
    range_a1: str | None = Field(default=None, min_length=1, max_length=160)
    sheet_id: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def exact_target(self):
        creation = self.action in {"create_doc", "upload_artifact"}
        if creation != (self.file_id is None):
            raise ValueError("Select an exact file except for new-file actions")
        if (
            self.action
            in {"read_sheet", "append_rows", "update_range", "set_formulas", "format_range"}
        ) != (self.range_a1 is not None):
            raise ValueError("Sheets actions require an explicit allowed A1 range")
        if self.range_a1 is not None:
            from backend.services.google.errors import GoogleServiceError
            from backend.services.google.ranges import parse_range

            try:
                bounds = parse_range(self.range_a1)
                if not bounds.tab or self.sheet_id is None:
                    raise ValueError("Select a named tab and its stable sheet ID")
            except GoogleServiceError as exc:
                raise ValueError(exc.detail) from exc
        elif self.sheet_id is not None:
            raise ValueError("Only Sheets grants use a tab ID")
        return self


class AutomationDraft(AutomationModel):
    name: str = Field(min_length=1, max_length=160)
    instruction: str = Field(min_length=1, max_length=20000)
    schedule: ScheduleSpec
    grants: list[GrantDraft] = Field(default_factory=list, max_length=40)
    artifact_version_ids: list[UUID] = Field(default_factory=list, max_length=20)
    artifact_actions: list[Literal["inspect", "create", "edit"]] = Field(
        default_factory=list, max_length=3
    )

    @field_validator("name", "instruction")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Name and instruction cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def unique_selections(self):
        if len(set(self.artifact_version_ids)) != len(self.artifact_version_ids) or len(
            set(self.artifact_actions)
        ) != len(self.artifact_actions):
            raise ValueError("Duplicate artifact selection")
        keys = [(g.connection_id, g.file_id, g.action, g.range_a1) for g in self.grants]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate grant")
        return self


class AutomationView(AutomationModel):
    id: UUID
    name: str
    enabled: bool
    revision_id: UUID
    revision_number: int
    instruction: str
    schedule: ScheduleSpec
    grants: list[GrantDraft]
    artifact_version_ids: list[UUID]
    artifact_actions: list[str]
    next_run_at: datetime | None


RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "cancelling",
    "needs_reconciliation",
    "succeeded",
    "failed",
    "cancelled",
    "skipped_overlap",
    "missed",
]


class AutomationRunView(AutomationModel):
    id: UUID
    automation_id: UUID
    revision_id: UUID
    trigger: str
    scheduled_for: datetime | None
    status: RunStatus
    job_id: UUID | None
    agent_run_id: UUID | None
    conversation_id: UUID | None
    stop_reason: str | None
    result: dict
    created_at: datetime
    finished_at: datetime | None


class EnabledInput(AutomationModel):
    enabled: bool = Field(strict=True)
    stop_active: bool = Field(default=False, strict=True)


class RunNowInput(AutomationModel):
    idempotency_key: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )


class CloseUnknownInput(AutomationModel):
    confirmed: Literal[True]
