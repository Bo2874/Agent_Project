import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.services import agent_jobs, run_events


async def test_worker_persists_a_small_chunk_before_requesting_the_next(monkeypatch):
    persisted = AsyncMock()
    monkeypatch.setattr(agent_jobs, "append_packet", persisted)
    first = 'event: delta\ndata: {"content":"one"}\n\n'
    second = 'event: delta\ndata: {"content":"two"}\n\n'

    async def packets():
        yield first
        persisted.assert_awaited_once_with(run, first)
        yield second

    run = uuid4()
    await agent_jobs.persist_agent_packets(run, packets())
    assert [call.args[1] for call in persisted.await_args_list] == [first, second]


async def test_persistence_failure_closes_the_model_stream_immediately(monkeypatch):
    closed = asyncio.Event()

    async def packets():
        try:
            yield 'event: delta\ndata: {"content":"one"}\n\n'
        finally:
            closed.set()

    monkeypatch.setattr(agent_jobs, "append_packet", AsyncMock(side_effect=OSError("offline")))
    stream = packets()
    try:
        with pytest.raises(OSError):
            await agent_jobs.persist_agent_packets(uuid4(), stream)
        assert closed.is_set()
    finally:
        await stream.aclose()


async def test_cancel_during_persistence_closes_the_model_stream(monkeypatch):
    closed, persisting = asyncio.Event(), asyncio.Event()

    async def packets():
        try:
            yield 'event: delta\ndata: {"content":"one"}\n\n'
        finally:
            closed.set()

    async def append(*args):
        persisting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_jobs, "append_packet", append)
    task = asyncio.create_task(agent_jobs.persist_agent_packets(uuid4(), packets()))
    await persisting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_persisted_event_and_notification_share_the_commit(monkeypatch):
    order = []
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(event_sequence=3)),
        add=lambda row: order.append(("event", row)),
        execute=AsyncMock(side_effect=lambda query: order.append(("notify", query))),
        commit=AsyncMock(side_effect=lambda: order.append(("commit", None))),
    )

    @asynccontextmanager
    async def runtime(**kwargs):
        yield session

    monkeypatch.setattr(run_events, "runtime_session", runtime)
    run = uuid4()
    await run_events.append_event(run, "delta", {"content": "small chunk"})
    assert [kind for kind, _ in order] == ["event", "notify", "commit"]
    assert order[0][1].sequence == 4
    assert run.hex in order[1][1].compile().params.values()
    assert "small chunk" not in str(order[1][1].compile().params)


async def test_subscriber_waits_for_notification_without_a_polling_sleep(monkeypatch):
    events = []
    changed, waiting = asyncio.Event(), asyncio.Event()

    class Signal:
        def clear(self):
            changed.clear()

        async def wait(self):
            waiting.set()
            await changed.wait()

    class Notifications:
        @asynccontextmanager
        async def subscribe(self, run_id):
            yield Signal()

    @asynccontextmanager
    async def session_factory():
        scalar_results = iter([SimpleNamespace(status="running"), uuid4()])
        yield SimpleNamespace(
            scalar=AsyncMock(side_effect=lambda query: next(scalar_results)),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: events.copy())),
        )

    monkeypatch.setattr(run_events, "SessionFactory", session_factory)
    monkeypatch.setattr(run_events, "notifications", Notifications(), raising=False)

    async def no_sleep(*args):
        pytest.fail("Normal event delivery must not wait for a polling timer")

    monkeypatch.setattr(run_events, "asyncio", SimpleNamespace(sleep=no_sleep), raising=False)
    stream = run_events.subscribe(uuid4(), uuid4(), uuid4(), after=1)
    assert await anext(stream) == ": heartbeat\n\n"
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        events.append(SimpleNamespace(sequence=2, kind="delta", data={"content": "now"}))
        changed.set()
        assert '"content": "now"' in await asyncio.wait_for(pending, 1)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()
