from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.schemas.document import DocumentCitationResponse


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=160)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Tin nhắn không được để trống")
        return value


class ToolApprovalDecision(BaseModel):
    approved: bool


class ToolApprovalResponse(BaseModel):
    approval_id: UUID
    approved: bool


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    created_at: datetime
    citations: list[DocumentCitationResponse] = Field(default_factory=list)


class ToolCallTimelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tool_name: str
    status: str
    duration_ms: int | None
    error_code: str | None
    approval_decision: str | None
    created_at: datetime
    finished_at: datetime | None


class RunStepTimelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    step_number: int
    attempt_number: int
    status: str
    summary: str | None
    model_name: str | None
    duration_ms: int | None
    total_tokens: int | None
    cached_input_tokens: int
    cache_miss_input_tokens: int
    prompt_cache_hit_rate: float | None
    prompt_version: str | None
    created_at: datetime
    finished_at: datetime | None
    tool_calls: list[ToolCallTimelineResponse]


class AgentRunTimelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_message_id: UUID | None
    assistant_message_id: UUID | None
    status: str
    current_step: int
    resume_count: int
    checkpoint_revision: int | None
    stop_reason: str | None
    total_tokens: int
    cached_input_tokens: int
    cache_miss_input_tokens: int
    prompt_cache_hit_rate: float | None
    prompt_version: str | None
    tool_selection_mode: str | None
    available_tool_count: int | None
    loaded_tool_count: int | None
    context_total_tokens: int | None
    context_summary_tokens: int | None
    context_budget_tokens: int | None
    context_trimmed_messages: int
    created_at: datetime
    finished_at: datetime | None
    steps: list[RunStepTimelineResponse]
    progress: list[dict[str, object]] = Field(default_factory=list)
    started_at: datetime | None = None
