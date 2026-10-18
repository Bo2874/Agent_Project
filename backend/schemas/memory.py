from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Memory không được để trống")
        return normalized


class MemoryPinRequest(BaseModel):
    is_pinned: bool


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    memory_type: str
    memory_key: str
    content: str
    source_type: str
    source_message_id: UUID | None
    source_tool_call_id: UUID | None
    source_reference: str | None
    confidence: Decimal
    observed_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    sensitivity: str
    retention_until: datetime
    is_pinned: bool
    created_at: datetime
    updated_at: datetime
