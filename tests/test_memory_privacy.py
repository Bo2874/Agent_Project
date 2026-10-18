from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_core.memory import (
    MemorySensitivity,
    classify_memory_sensitivity,
    memory_retention_deadline,
)
from backend.services.memory import list_user_memories, purge_expired_memories


class ScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class ListSession:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    async def scalars(self, _statement: object) -> ScalarResult:
        return ScalarResult(self.values)


class DeleteResult:
    rowcount = 3


class PurgeSession:
    def __init__(self) -> None:
        self.statement: object | None = None
        self.commits = 0

    async def execute(self, statement: object) -> DeleteResult:
        self.statement = statement
        return DeleteResult()

    async def commit(self) -> None:
        self.commits += 1


def stored_memory(
    *,
    user_id: object,
    retention_until: datetime,
    is_pinned: bool = False,
    status: str = "active",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        status=status,
        is_pinned=is_pinned,
        retention_until=retention_until,
        updated_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("key", "content", "expected"),
    [
        ("auth.password", "abc123", MemorySensitivity.RESTRICTED),
        ("payment.card", "4111 1111 1111 1111", MemorySensitivity.RESTRICTED),
        ("profile.email", "user@example.com", MemorySensitivity.SENSITIVE),
        ("health.condition", "Hen suyễn", MemorySensitivity.SENSITIVE),
        ("profile.city", "Đà Nẵng", MemorySensitivity.PERSONAL),
        ("response.style", "Ngắn gọn", MemorySensitivity.NORMAL),
    ],
)
def test_memory_sensitivity_classification(
    key: str,
    content: str,
    expected: MemorySensitivity,
) -> None:
    assert classify_memory_sensitivity(key, content) == expected


def test_retention_deadline_depends_on_sensitivity() -> None:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)

    assert memory_retention_deadline(
        observed_at, MemorySensitivity.NORMAL
    ) == observed_at + timedelta(days=365)
    assert memory_retention_deadline(
        observed_at, MemorySensitivity.PERSONAL
    ) == observed_at + timedelta(days=180)
    assert memory_retention_deadline(
        observed_at, MemorySensitivity.SENSITIVE
    ) == observed_at + timedelta(days=30)
    with pytest.raises(ValueError, match="restricted"):
        memory_retention_deadline(observed_at, MemorySensitivity.RESTRICTED)


async def test_memory_list_isolated_by_user_and_retention() -> None:
    now = datetime.now(UTC)
    user_id = uuid4()
    active = stored_memory(user_id=user_id, retention_until=now + timedelta(days=1))
    expired = stored_memory(user_id=user_id, retention_until=now - timedelta(seconds=1))
    pinned_expired = stored_memory(
        user_id=user_id,
        retention_until=now - timedelta(days=1),
        is_pinned=True,
    )
    other_user = stored_memory(
        user_id=uuid4(),
        retention_until=now + timedelta(days=1),
    )

    result = await list_user_memories(  # type: ignore[arg-type]
        ListSession([active, expired, pinned_expired, other_user]),
        user_id,
        now=now,
    )

    assert result == [active, pinned_expired]


async def test_purge_targets_only_expired_unpinned_memories() -> None:
    session = PurgeSession()
    now = datetime.now(UTC)

    deleted = await purge_expired_memories(  # type: ignore[arg-type]
        session,
        user_id=uuid4(),
        now=now,
    )

    statement = str(session.statement)
    assert deleted == 3
    assert session.commits == 1
    assert "DELETE FROM user_memories" in statement
    assert "user_memories.is_pinned IS false" in statement
    assert "user_memories.retention_until <=" in statement
    assert "user_memories.user_id =" in statement
