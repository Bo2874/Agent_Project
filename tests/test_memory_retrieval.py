from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from agent_core.context_budget import measure_context_blocks
from agent_core.memory import rank_relevant_memories
from backend.services.memory import retrieve_memory_context


class ScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeSession:
    def __init__(self, memories: list[object]) -> None:
        self.memories = memories

    async def scalars(self, _statement: object) -> ScalarResult:
        return ScalarResult(self.memories)


def memory(
    *,
    user_id: object,
    memory_type: str,
    key: str,
    content: str,
    observed_at: datetime,
    confidence: str = "0.900",
    status: str = "active",
    source_message_id: object | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    retention_until: datetime | None = None,
    is_pinned: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        memory_type=memory_type,
        memory_key=key,
        content=content,
        confidence=Decimal(confidence),
        status=status,
        source_message_id=source_message_id,
        observed_at=observed_at,
        valid_from=valid_from,
        valid_until=valid_until,
        sensitivity="personal",
        retention_until=retention_until or observed_at + timedelta(days=180),
        is_pinned=is_pinned,
    )


def test_rank_relevant_memories_prioritizes_query_match_and_preferences() -> None:
    now = datetime.now(UTC)
    user_id = uuid4()
    location = memory(
        user_id=user_id,
        memory_type="fact",
        key="profile.location",
        content="Đà Nẵng",
        observed_at=now,
    )
    preference = memory(
        user_id=user_id,
        memory_type="preference",
        key="response.style",
        content="Trả lời ngắn gọn",
        observed_at=now,
    )
    unrelated = memory(
        user_id=user_id,
        memory_type="fact",
        key="work.company",
        content="Công ty Example",
        observed_at=now,
    )

    ranked = rank_relevant_memories(
        [unrelated, preference, location],
        "Tôi đang sống ở thành phố nào?",
        now=now,
    )

    assert [item.memory for item in ranked] == [location, preference]
    assert "location" in ranked[0].matched_terms


async def test_retrieval_filters_user_status_time_source_and_irrelevant_memory() -> None:
    now = datetime.now(UTC)
    user_id = uuid4()
    other_user_id = uuid4()
    current_message_id = uuid4()
    memories = [
        memory(
            user_id=user_id,
            memory_type="fact",
            key="profile.location",
            content="Đà Nẵng",
            observed_at=now,
        ),
        memory(
            user_id=user_id,
            memory_type="preference",
            key="response.style",
            content="Trả lời ngắn gọn",
            observed_at=now,
        ),
        memory(
            user_id=user_id,
            memory_type="fact",
            key="work.company",
            content="Công ty Example",
            observed_at=now,
        ),
        memory(
            user_id=other_user_id,
            memory_type="fact",
            key="profile.location",
            content="Hà Nội",
            observed_at=now,
        ),
        memory(
            user_id=user_id,
            memory_type="fact",
            key="profile.old_city",
            content="Huế",
            observed_at=now,
            valid_until=now - timedelta(seconds=1),
        ),
        memory(
            user_id=user_id,
            memory_type="fact",
            key="profile.current_message",
            content="Không được nạp lại",
            observed_at=now,
            source_message_id=current_message_id,
        ),
    ]

    result = await retrieve_memory_context(  # type: ignore[arg-type]
        FakeSession(memories),
        user_id=user_id,
        query="Tôi đang sống ở thành phố nào?",
        token_budget=1_000,
        min_confidence=Decimal("0.600"),
        max_items=10,
        candidate_limit=100,
        excluded_source_message_id=current_message_id,
        now=now,
    )

    rendered = "\n".join(block.content for block in result.blocks)
    assert result.candidate_count == 3
    assert result.relevant_count == 2
    assert result.selected_count == 2
    assert "Đà Nẵng" in rendered
    assert "Trả lời ngắn gọn" in rendered
    assert "Công ty Example" not in rendered
    assert "Hà Nội" not in rendered
    assert "Huế" not in rendered
    assert "Không được nạp lại" not in rendered
    assert result.token_count == measure_context_blocks("memory", result.blocks)
    assert result.token_count <= 1_000


async def test_retrieval_returns_no_block_when_memory_budget_is_too_small() -> None:
    now = datetime.now(UTC)
    user_id = uuid4()
    session = FakeSession(
        [
            memory(
                user_id=user_id,
                memory_type="preference",
                key="response.style",
                content="Trả lời ngắn gọn",
                observed_at=now,
            )
        ]
    )

    result = await retrieve_memory_context(  # type: ignore[arg-type]
        session,
        user_id=user_id,
        query="Xin chào",
        token_budget=1,
        min_confidence=Decimal("0.600"),
        max_items=10,
        candidate_limit=100,
        now=now,
    )

    assert result.selected_count == 0
    assert result.token_count == 0
    assert result.blocks == ()


async def test_retrieval_excludes_expired_memory_but_keeps_pinned_memory() -> None:
    now = datetime.now(UTC)
    user_id = uuid4()
    expired = memory(
        user_id=user_id,
        memory_type="preference",
        key="response.style",
        content="Không còn được dùng",
        observed_at=now - timedelta(days=200),
        retention_until=now - timedelta(seconds=1),
    )
    pinned = memory(
        user_id=user_id,
        memory_type="preference",
        key="response.language",
        content="Tiếng Việt",
        observed_at=now - timedelta(days=200),
        retention_until=now - timedelta(seconds=1),
        is_pinned=True,
    )

    result = await retrieve_memory_context(  # type: ignore[arg-type]
        FakeSession([expired, pinned]),
        user_id=user_id,
        query="Hãy trả lời tôi",
        token_budget=1_000,
        min_confidence=Decimal("0.600"),
        max_items=10,
        candidate_limit=100,
        now=now,
    )

    rendered = "\n".join(block.content for block in result.blocks)
    assert result.candidate_count == 1
    assert "Tiếng Việt" in rendered
    assert "Không còn được dùng" not in rendered
