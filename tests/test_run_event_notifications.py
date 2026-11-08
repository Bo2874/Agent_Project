import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.services import run_event_notifications as signals


class Connection:
    def __init__(self):
        self.closed = False
        self.listener = None
        self.termination = None
        self.close = AsyncMock(side_effect=self.close_connection)

    async def close_connection(self, **kwargs):
        self.closed = True

    async def add_listener(self, channel, listener):
        assert channel == signals.CHANNEL
        self.listener = listener

    def add_termination_listener(self, callback):
        self.termination = callback

    def is_closed(self):
        return self.closed

    def terminate(self):
        self.closed = True


@pytest.fixture
def listener(monkeypatch):
    connection = Connection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(signals.asyncpg, "connect", connect)
    monkeypatch.setattr(
        signals,
        "get_settings",
        lambda: SimpleNamespace(
            db_host="test",
            db_port=1,
            db_user="test",
            db_name="test",
            db_password=SimpleNamespace(get_secret_value=lambda: "fake"),
        ),
    )
    return signals.RunEventNotifications(), connection, connect


async def test_multiple_subscribers_share_one_listener_and_close_it_after_the_last(listener):
    hub, connection, connect = listener
    a, b = uuid4(), uuid4()
    async with hub.subscribe(a) as first:
        assert connection.listener is not None
        async with hub.subscribe(b) as second:
            connect.assert_awaited_once()
            connection.listener(connection, 1, signals.CHANNEL, a.hex)
            assert first.event.is_set()
            assert not second.event.is_set()
        connection.close.assert_not_awaited()
    connection.close.assert_awaited_once()
    assert not hub.events and hub.connection is None


async def test_notify_between_catchup_query_and_wait_is_not_lost(listener):
    hub, connection, _ = listener
    run = uuid4()
    async with hub.subscribe(run) as signal:
        signal.clear()
        connection.listener(connection, 1, signals.CHANNEL, run.hex)
        await asyncio.wait_for(signal.wait(), 1)


async def test_disconnected_listener_reconnects_and_triggers_immediate_catchup(listener):
    hub, connection, connect = listener
    replacement = Connection()
    async with hub.subscribe(uuid4()) as signal:
        connection.closed = True
        connection.termination(connection)
        assert signal.event.is_set()
        signal.clear()
        connect.return_value = replacement
        # A reconnection returns immediately, even without a fresh notification.
        await asyncio.wait_for(signal.wait(), 1)
        assert hub.connection is replacement
        assert connect.await_count == 2
    replacement.close.assert_awaited_once()


async def test_reconnecting_one_subscriber_wakes_every_other_catchup_query(listener):
    hub, connection, connect = listener
    async with hub.subscribe(uuid4()) as first, hub.subscribe(uuid4()) as second:
        connection.closed = True
        connection.termination(connection)
        # Both subscribers queried while disconnected; only the first reconnects.
        first.clear()
        second.clear()
        connect.return_value = Connection()
        await first.wait()
        assert second.event.is_set()


async def test_listener_failure_uses_fallback_and_backs_off_reconnection(listener, monkeypatch):
    hub, connection, connect = listener
    connect.side_effect = OSError("offline")
    sleep = AsyncMock()
    monkeypatch.setattr(signals.asyncio, "sleep", sleep)
    async with hub.subscribe(uuid4()) as signal:
        assert hub.connection is None
        await signal.wait()
        sleep.assert_awaited_once_with(signals.FALLBACK_SECONDS)
        connect.assert_awaited_once()
        hub.next_retry = 0
        connect.side_effect = None
        await signal.wait()
        assert hub.connection is connection
    connection.close.assert_awaited_once()


async def test_partial_listener_setup_failure_terminates_the_connection(listener):
    hub, connection, _ = listener
    connection.add_listener = AsyncMock(side_effect=OSError("disconnected"))
    async with hub.subscribe(uuid4()):
        assert connection.closed
        assert hub.connection is None


async def test_cancelled_subscriber_releases_listener_and_registration(listener):
    hub, connection, _ = listener
    waiting = asyncio.Event()

    async def subscribe():
        async with hub.subscribe(uuid4()) as signal:
            waiting.set()
            await signal.wait()

    task = asyncio.create_task(subscribe())
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not hub.events and hub.connection is None
    connection.close.assert_awaited_once()
