"""PostgreSQL wakeups; persisted owner-scoped RunEvents remain authoritative."""

import asyncio
import logging
from contextlib import asynccontextmanager
from time import monotonic
from uuid import UUID

import asyncpg
from sqlalchemy import func, select

from backend.core.config import get_settings

CHANNEL = "agent_run_events"
HEARTBEAT_SECONDS = 15
FALLBACK_SECONDS = 0.1
RECONNECT_SECONDS = 5
logger = logging.getLogger(__name__)


async def notify_run_event(session, run_id):
    """Queue a wakeup on the same transaction as its durable event."""
    await session.execute(select(func.pg_notify(CHANNEL, UUID(str(run_id)).hex)))


class RunEventSignal:
    def __init__(self, hub, event):
        self.hub, self.event = hub, event

    def clear(self):
        self.event.clear()

    async def wait(self):
        async with self.hub.lock:
            reconnected = await self.hub.connect()
            connection = self.hub.connection
        if reconnected:
            # Catch events committed while the listener was disconnected.
            return
        if connection is None:
            await asyncio.sleep(FALLBACK_SECONDS)
            return
        try:
            await asyncio.wait_for(self.event.wait(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            pass


class RunEventNotifications:
    """One LISTEN connection per process, shared by concurrent SSE subscriptions."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.events = {}
        self.connection = None
        self.next_retry = 0

    def notified(self, connection, pid, channel, payload):
        for event in tuple(self.events.get(payload, ())):
            event.set()

    def disconnected(self, connection):
        if self.connection is connection:
            self.connection = None
            self.next_retry = 0
            for listeners in self.events.values():
                for event in listeners:
                    event.set()

    async def connect(self):
        # Caller holds lock. LISTEN is installed before any catch-up query.
        if self.connection is not None and not self.connection.is_closed():
            return False
        if monotonic() < self.next_retry:
            return False
        connection = None
        try:
            settings = get_settings()
            connection = await asyncpg.connect(
                host=settings.db_host,
                port=settings.db_port,
                user=settings.db_user,
                password=settings.db_password.get_secret_value(),
                database=settings.db_name,
                timeout=2,
                command_timeout=2,
            )
            await connection.add_listener(CHANNEL, self.notified)
            connection.add_termination_listener(self.disconnected)
        except asyncio.CancelledError:
            if connection is not None:
                connection.terminate()
            raise
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
            if connection is not None:
                connection.terminate()
            self.next_retry = monotonic() + RECONNECT_SECONDS
            logger.warning("Run-event listener unavailable; using database catch-up")
            return False
        self.connection = connection
        self.next_retry = 0
        # Every subscriber must catch up on events committed during the gap,
        # including those whose query ran while another subscriber reconnected.
        for listeners in self.events.values():
            for event in listeners:
                event.set()
        return True

    @asynccontextmanager
    async def subscribe(self, run_id):
        key, event = UUID(str(run_id)).hex, asyncio.Event()
        try:
            async with self.lock:
                self.events.setdefault(key, set()).add(event)
                await self.connect()
                # This new subscriber has not queried yet. Existing subscribers
                # retain the reconnect wakeup; its first query catches the gap.
                event.clear()
            yield RunEventSignal(self, event)
        finally:
            async with self.lock:
                listeners = self.events.get(key)
                if listeners is not None:
                    listeners.discard(event)
                    if not listeners:
                        del self.events[key]
                if not self.events and self.connection is not None:
                    connection, self.connection = self.connection, None
                    try:
                        await connection.close(timeout=1)
                    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError):
                        connection.terminate()


notifications = RunEventNotifications()
