from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_core.llm.types import LLMChunk, TokenUsage
from agent_core.memory import (
    MemoryCandidate,
    MemoryExtractionError,
    MemoryType,
    extract_memory_candidates,
    parse_memory_candidates,
)
from backend.services.memory import (
    MemorySourceOwnershipError,
    extract_and_store_user_message_memories,
    resolve_memory_candidates,
)


class FakeModel:
    provider = "fake"
    model = "fake-memory-model"

    def __init__(self, chunks: list[LLMChunk]) -> None:
        self.chunks = chunks
        self.received_user_id: str | None = None

    async def stream(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
        user_id: str | None = None,
    ):  # type: ignore[no-untyped-def]
        assert messages[0]["role"] == "system"
        assert tools is None
        self.received_user_id = user_id
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class FakeSession:
    def __init__(self, scalar_values: list[object]) -> None:
        self.scalar_values = scalar_values
        self.added: list[object] = []

    async def scalar(self, _statement: object) -> object:
        return self.scalar_values.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)


def candidate(
    content: str,
    *,
    confidence: str = "0.900",
    key: str = "profile.city",
) -> MemoryCandidate:
    return MemoryCandidate(
        memory_type=MemoryType.FACT,
        key=key,
        content=content,
        confidence=Decimal(confidence),
    )


def existing_memory(content: str, *, confidence: str = "0.800") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        content=content,
        confidence=Decimal(confidence),
        status="active",
        superseded_by_id=None,
    )


def test_parse_memory_candidates_normalizes_deduplicates_and_blocks_secrets() -> None:
    raw = """```json
    {"memories":[
      {"memory_type":"fact","key":"Profile.City","content":"Đà Nẵng","confidence":0.7},
      {"memory_type":"fact","key":"profile city","content":"Đà Nẵng","confidence":0.9},
      {"memory_type":"preference","key":"auth.password","content":"abc123","confidence":1},
      {"memory_type":"goal","key":"project.goal","content":"Ship","confidence":0.8}
    ]}
    ```"""

    result = parse_memory_candidates(raw)

    assert len(result) == 1
    assert result[0].key == "profile.city"
    assert result[0].content == "Đà Nẵng"
    assert result[0].confidence == Decimal("0.900")


def test_parse_memory_candidates_rejects_non_json_output() -> None:
    with pytest.raises(MemoryExtractionError, match="JSON"):
        parse_memory_candidates("không có memory")


async def test_extract_memory_candidates_collects_stream_and_usage() -> None:
    user_id = uuid4()
    model = FakeModel(
        [
            LLMChunk(content='{"memories":[{"memory_type":"preference",'),
            LLMChunk(
                content=('"key":"response.style","content":"ngắn gọn","confidence":0.85}]}'),
                usage=TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            ),
        ]
    )

    result = await extract_memory_candidates(model, "Trả lời ngắn gọn", user_id=user_id)

    assert len(result.candidates) == 1
    assert result.candidates[0].memory_type == MemoryType.PREFERENCE
    assert result.usage.total_tokens == 30
    assert model.received_user_id == str(user_id)


async def test_restricted_user_message_skips_memory_extraction_model_call() -> None:
    model = FakeModel([])

    summary = await extract_and_store_user_message_memories(
        model=model,
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        user_message_content="API key của tôi là sk-secret-value",
        observed_at=datetime.now(UTC),
        min_confidence=Decimal("0.600"),
        timeout_seconds=1,
    )

    assert summary.blocked_sensitive == 1
    assert model.received_user_id is None


async def test_new_memory_is_created_for_owned_message() -> None:
    user_id = uuid4()
    message_id = uuid4()
    session = FakeSession([message_id, None])

    summary = await resolve_memory_candidates(  # type: ignore[arg-type]
        session,
        user_id=user_id,
        user_message_id=message_id,
        observed_at=datetime.now(UTC),
        candidates=(candidate("Đà Nẵng"),),
        min_confidence=Decimal("0.600"),
    )

    assert summary.created == 1
    assert len(session.added) == 1
    memory = session.added[0]
    assert memory.user_id == user_id  # type: ignore[attr-defined]
    assert memory.status == "active"  # type: ignore[attr-defined]
    assert memory.sensitivity == "personal"  # type: ignore[attr-defined]
    assert memory.retention_until > memory.observed_at  # type: ignore[attr-defined]


async def test_restricted_memory_is_blocked_again_before_database_write() -> None:
    message_id = uuid4()
    session = FakeSession([message_id])

    summary = await resolve_memory_candidates(  # type: ignore[arg-type]
        session,
        user_id=uuid4(),
        user_message_id=message_id,
        observed_at=datetime.now(UTC),
        candidates=(candidate("abc123", key="auth.password"),),
        min_confidence=Decimal("0.600"),
    )

    assert summary.extracted == 1
    assert summary.blocked_sensitive == 1
    assert session.added == []


async def test_duplicate_memory_does_not_create_another_row() -> None:
    message_id = uuid4()
    session = FakeSession([message_id, existing_memory("  ĐÀ NẴNG ")])

    summary = await resolve_memory_candidates(  # type: ignore[arg-type]
        session,
        user_id=uuid4(),
        user_message_id=message_id,
        observed_at=datetime.now(UTC),
        candidates=(candidate("Đà Nẵng"),),
        min_confidence=Decimal("0.600"),
    )

    assert summary.duplicates == 1
    assert session.added == []


async def test_stronger_conflict_supersedes_active_memory() -> None:
    message_id = uuid4()
    existing = existing_memory("Hà Nội", confidence="0.700")
    session = FakeSession([message_id, existing])

    summary = await resolve_memory_candidates(  # type: ignore[arg-type]
        session,
        user_id=uuid4(),
        user_message_id=message_id,
        observed_at=datetime.now(UTC),
        candidates=(candidate("Đà Nẵng", confidence="0.900"),),
        min_confidence=Decimal("0.600"),
    )

    replacement = session.added[0]
    assert summary.conflicts_replaced == 1
    assert existing.status == "superseded"
    assert existing.superseded_by_id == replacement.id  # type: ignore[attr-defined]
    assert replacement.status == "active"  # type: ignore[attr-defined]


async def test_weaker_conflict_is_kept_only_as_inactive_audit_row() -> None:
    message_id = uuid4()
    existing = existing_memory("Hà Nội", confidence="0.950")
    session = FakeSession([message_id, existing])

    summary = await resolve_memory_candidates(  # type: ignore[arg-type]
        session,
        user_id=uuid4(),
        user_message_id=message_id,
        observed_at=datetime.now(UTC),
        candidates=(candidate("Đà Nẵng", confidence="0.700"),),
        min_confidence=Decimal("0.600"),
    )

    rejected = session.added[0]
    assert summary.conflicts_kept == 1
    assert existing.status == "active"
    assert rejected.status == "superseded"  # type: ignore[attr-defined]
    assert rejected.superseded_by_id == existing.id  # type: ignore[attr-defined]


async def test_memory_source_must_belong_to_user() -> None:
    session = FakeSession([None])

    with pytest.raises(MemorySourceOwnershipError):
        await resolve_memory_candidates(  # type: ignore[arg-type]
            session,
            user_id=uuid4(),
            user_message_id=uuid4(),
            observed_at=datetime.now(UTC),
            candidates=(candidate("Đà Nẵng"),),
            min_confidence=Decimal("0.600"),
        )
