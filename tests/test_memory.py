from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint

from agent_core.memory import MemorySourceType, MemoryType, UserMemorySpec
from backend.models import UserMemory


def test_user_memory_model_tracks_type_source_time_and_confidence() -> None:
    table = UserMemory.__table__

    assert {
        "user_id",
        "memory_type",
        "memory_key",
        "content",
        "source_type",
        "source_message_id",
        "source_tool_call_id",
        "source_reference",
        "confidence",
        "observed_at",
        "valid_from",
        "valid_until",
        "status",
        "sensitivity",
        "retention_until",
        "is_pinned",
        "superseded_by_id",
        "created_at",
        "updated_at",
    } <= set(table.c.keys())
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_user_memories_type",
        "ck_user_memories_source_type",
        "ck_user_memories_status",
        "ck_user_memories_sensitivity",
        "ck_user_memories_confidence",
        "ck_user_memories_valid_range",
        "ck_user_memories_source_link",
    } <= check_names


def test_user_memory_model_has_provenance_and_lookup_indexes() -> None:
    table = UserMemory.__table__
    foreign_keys = {
        column.name: next(iter(column.foreign_keys)).target_fullname
        for column in (
            table.c.user_id,
            table.c.source_message_id,
            table.c.source_tool_call_id,
            table.c.superseded_by_id,
        )
    }
    indexes = {
        index.name: tuple(column.name for column in index.columns) for index in table.indexes
    }

    assert foreign_keys == {
        "user_id": "users.id",
        "source_message_id": "messages.id",
        "source_tool_call_id": "tool_calls.id",
        "superseded_by_id": "user_memories.id",
    }
    assert indexes["ix_user_memories_user_key"] == ("user_id", "memory_key")
    assert indexes["ix_user_memories_user_type_status"] == (
        "user_id",
        "memory_type",
        "status",
    )
    assert indexes["ix_user_memories_user_retention"] == (
        "user_id",
        "retention_until",
    )
    assert indexes["uq_user_memories_active_key"] == (
        "user_id",
        "memory_type",
        "memory_key",
    )
    assert next(
        index for index in table.indexes if index.name == "uq_user_memories_active_key"
    ).unique


def test_memory_spec_accepts_traceable_user_message_source() -> None:
    observed_at = datetime.now(UTC)
    spec = UserMemorySpec(
        memory_type=MemoryType.PREFERENCE,
        key="response.style",
        content="Người dùng thích câu trả lời ngắn gọn",
        source_type=MemorySourceType.USER_MESSAGE,
        source_message_id=uuid4(),
        confidence=Decimal("0.900"),
        observed_at=observed_at,
        valid_from=observed_at,
        valid_until=observed_at + timedelta(days=90),
    )

    assert spec.confidence == Decimal("0.900")
    assert spec.memory_type == MemoryType.PREFERENCE


@pytest.mark.parametrize("confidence", [Decimal("-0.001"), Decimal("1.001")])
def test_memory_spec_rejects_confidence_outside_probability_range(
    confidence: Decimal,
) -> None:
    with pytest.raises(ValueError, match="confidence"):
        UserMemorySpec(
            memory_type=MemoryType.FACT,
            key="profile.city",
            content="Đà Nẵng",
            source_type=MemorySourceType.MANUAL,
            confidence=confidence,
            observed_at=datetime.now(UTC),
        )


def test_memory_spec_rejects_source_link_that_does_not_match_type() -> None:
    with pytest.raises(ValueError, match="source_type"):
        UserMemorySpec(
            memory_type=MemoryType.FACT,
            key="profile.city",
            content="Đà Nẵng",
            source_type=MemorySourceType.TOOL_RESULT,
            source_message_id=uuid4(),
            confidence=Decimal("0.750"),
            observed_at=datetime.now(UTC),
        )


def test_memory_spec_rejects_invalid_validity_window() -> None:
    observed_at = datetime.now(UTC)
    with pytest.raises(ValueError, match="valid_until"):
        UserMemorySpec(
            memory_type=MemoryType.GOAL,
            key="project.deadline",
            content="Hoàn thành trước tháng 12",
            source_type=MemorySourceType.IMPORT,
            source_reference="calendar-import",
            confidence=Decimal("0.800"),
            observed_at=observed_at,
            valid_from=observed_at,
            valid_until=observed_at - timedelta(days=1),
        )
