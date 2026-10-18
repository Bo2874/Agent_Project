from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.main import app
from backend.services.memory import (
    MemoryNotFoundError,
    SensitiveMemoryRejectedError,
    delete_user_memory,
    set_memory_pinned,
    update_user_memory,
)


class FakeSession:
    def __init__(self, scalar_values: list[object]) -> None:
        self.scalar_values = scalar_values
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.flushes = 0
        self.commits = 0
        self.refreshes = 0

    async def scalar(self, _statement: object) -> object:
        return self.scalar_values.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _value: object) -> None:
        self.refreshes += 1

    async def delete(self, value: object) -> None:
        self.deleted.append(value)


def active_memory(*, content: str = "Đà Nẵng", is_pinned: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        memory_type="fact",
        memory_key="profile.location",
        content=content,
        confidence=Decimal("0.800"),
        status="active",
        source_message_id=uuid4(),
        source_tool_call_id=None,
        source_reference="memory-extraction-v1",
        observed_at=datetime.now(UTC),
        valid_from=None,
        valid_until=None,
        sensitivity="personal",
        retention_until=datetime.now(UTC),
        is_pinned=is_pinned,
        superseded_by_id=None,
    )


async def test_user_edit_creates_manual_version_and_supersedes_previous() -> None:
    current = active_memory()
    session = FakeSession([current])

    replacement = await update_user_memory(  # type: ignore[arg-type]
        session,
        user_id=current.user_id,
        memory_id=current.id,
        content="Hà Nội",
    )

    assert session.added == [replacement]
    assert session.flushes == 2
    assert session.commits == 1
    assert replacement.content == "Hà Nội"
    assert replacement.source_type == "manual"
    assert replacement.confidence == Decimal("1.000")
    assert replacement.sensitivity == "personal"
    assert replacement.retention_until > replacement.observed_at
    assert replacement.is_pinned is True
    assert replacement.status == "active"
    assert current.status == "superseded"
    assert current.superseded_by_id == replacement.id


async def test_unchanged_edit_does_not_create_new_version() -> None:
    current = active_memory(content="Đà Nẵng")
    session = FakeSession([current])

    result = await update_user_memory(  # type: ignore[arg-type]
        session,
        user_id=current.user_id,
        memory_id=current.id,
        content="  ĐÀ NẴNG  ",
    )

    assert result is current
    assert session.added == []
    assert session.commits == 0


async def test_user_can_pin_and_delete_owned_active_memory() -> None:
    current = active_memory(is_pinned=False)
    pin_session = FakeSession([current])

    pinned = await set_memory_pinned(  # type: ignore[arg-type]
        pin_session,
        user_id=current.user_id,
        memory_id=current.id,
        is_pinned=True,
    )

    assert pinned.is_pinned is True
    assert pin_session.commits == 1
    delete_session = FakeSession([current])
    await delete_user_memory(  # type: ignore[arg-type]
        delete_session,
        user_id=current.user_id,
        memory_id=current.id,
    )
    assert delete_session.deleted == [current]
    assert delete_session.commits == 1


async def test_missing_or_other_user_memory_is_not_exposed() -> None:
    session = FakeSession([None])

    with pytest.raises(MemoryNotFoundError):
        await set_memory_pinned(  # type: ignore[arg-type]
            session,
            user_id=uuid4(),
            memory_id=uuid4(),
            is_pinned=True,
        )


async def test_other_user_memory_returned_by_storage_is_still_rejected() -> None:
    current = active_memory()
    session = FakeSession([current])

    with pytest.raises(MemoryNotFoundError):
        await set_memory_pinned(  # type: ignore[arg-type]
            session,
            user_id=uuid4(),
            memory_id=current.id,
            is_pinned=True,
        )


async def test_manual_edit_rejects_credentials() -> None:
    current = active_memory()
    session = FakeSession([current])

    with pytest.raises(SensitiveMemoryRejectedError):
        await update_user_memory(  # type: ignore[arg-type]
            session,
            user_id=current.user_id,
            memory_id=current.id,
            content="API key của tôi là sk-secret-value",
        )

    assert session.added == []
    assert session.commits == 0


def test_memory_management_routes_are_registered() -> None:
    paths = app.openapi()["paths"]

    assert "get" in paths["/api/memories"]
    assert "patch" in paths["/api/memories/{memory_id}"]
    assert "delete" in paths["/api/memories/{memory_id}"]
    assert "patch" in paths["/api/memories/{memory_id}/pin"]
