"""Opt-in delivery integration against an explicitly isolated migrated PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.background.queue import PostgresJobQueue
from backend.core.config import Settings
from backend.models import AgentRun, BackgroundJob, Conversation, Message, User
from backend.services import agent_jobs, run_event_notifications, run_events


@pytest.fixture
async def postgres(monkeypatch):
    port = os.environ.get("RUN_EVENT_TEST_POSTGRES_PORT")
    if not port:
        pytest.skip("Requires an explicitly isolated migrated notification test database")
    settings = Settings(
        _env_file=None,
        db_host="127.0.0.1",
        db_port=int(port),
        db_name="agent_stream_qa",
        db_user="postgres",
        db_password="notify-qa-only",
        auth_secret_key="offline-test-only-key-32-characters",
    )
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hub = run_event_notifications.RunEventNotifications()
    monkeypatch.setattr(run_event_notifications, "get_settings", lambda: settings)
    monkeypatch.setattr(run_events, "notifications", hub)
    monkeypatch.setattr(run_events, "SessionFactory", sessions)
    owner = User(id=uuid4(), email=f"{uuid4()}@example.com", password_hash="fake")
    conversation = Conversation(id=uuid4(), user_id=owner.id)
    message = Message(id=uuid4(), conversation_id=conversation.id, role="user", content="test")
    run = AgentRun(
        id=uuid4(), conversation_id=conversation.id, user_message_id=message.id, status="running"
    )
    async with sessions() as session:
        session.add(owner)
        await session.flush()
        session.add(conversation)
        await session.flush()
        session.add(message)
        await session.flush()
        session.add(run)
        await session.commit()
    try:
        yield settings, sessions, hub, owner, conversation, run
    finally:
        async with sessions() as session:
            await session.delete(await session.get(User, owner.id))
            await session.commit()
        await engine.dispose()


async def test_small_worker_chunks_wake_sse_immediately_and_replay_in_order(postgres):
    _, _, hub, owner, conversation, run = postgres
    release = asyncio.Event()

    async def packets():
        yield 'event: delta\ndata: {"content":"first"}\n\n'
        await release.wait()
        yield 'event: delta\ndata: {"content":"second"}\n\n'
        yield "event: done\ndata: {}\n\n"

    stream = run_events.subscribe(owner.id, conversation.id, run.id)
    assert await anext(stream) == ": heartbeat\n\n"
    producer = asyncio.create_task(agent_jobs.persist_agent_packets(run.id, packets()))
    try:
        packet = await asyncio.wait_for(anext(stream), 2)
        assert '"content": "first"' in packet
        assert not producer.done()  # Second provider chunk has not been generated.
        release.set()
        tail = [packet async for packet in stream]
        await producer
        assert len(tail) == 2
        assert '"content": "second"' in tail[0]
        assert "event: done" in tail[1]
        assert not hub.events and hub.connection is None
        replay = [
            packet
            async for packet in run_events.subscribe(owner.id, conversation.id, run.id, after=1)
        ]
        assert [packet.splitlines()[0] for packet in replay] == [
            f"id: {run.id}:2",
            f"id: {run.id}:3",
        ]
    finally:
        release.set()
        await asyncio.gather(producer, return_exceptions=True)
        await stream.aclose()


async def test_notifications_only_arrive_after_commit_and_ignore_rollback(postgres):
    settings, _, hub, _, _, run = postgres
    connection = await asyncpg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        database=settings.db_name,
    )
    try:
        async with hub.subscribe(run.id) as signal:
            transaction = connection.transaction()
            await transaction.start()
            await connection.execute(
                "SELECT pg_notify($1, $2)", run_event_notifications.CHANNEL, run.id.hex
            )
            assert not signal.event.is_set()
            await transaction.commit()
            await asyncio.wait_for(signal.wait(), 2)
            assert signal.event.is_set()
            signal.clear()
            transaction = connection.transaction()
            await transaction.start()
            await connection.execute(
                "SELECT pg_notify($1, $2)", run_event_notifications.CHANNEL, run.id.hex
            )
            await transaction.rollback()
            # A second subscribed channel key acts as a delivery barrier.
            other = uuid4()
            async with hub.subscribe(other) as barrier:
                await connection.execute(
                    "SELECT pg_notify($1, $2)", run_event_notifications.CHANNEL, other.hex
                )
                await asyncio.wait_for(barrier.wait(), 2)
                assert not signal.event.is_set()
    finally:
        await connection.close()


async def test_other_owner_receives_no_replayed_data(postgres):
    _, _, hub, _, conversation, run = postgres
    await run_events.append_event(run.id, "delta", {"content": "private"})
    packets = [packet async for packet in run_events.subscribe(uuid4(), conversation.id, run.id)]
    assert packets == []
    assert not hub.events and hub.connection is None


async def test_expired_worker_terminal_event_wakes_the_existing_subscriber(postgres, monkeypatch):
    from backend.background import queue

    _, sessions, hub, owner, conversation, run = postgres
    monkeypatch.setattr(queue, "SessionFactory", sessions)
    job = BackgroundJob(
        id=uuid4(),
        user_id=owner.id,
        agent_run_id=run.id,
        job_type="agent.run",
        queue_name="notify-qa",
        status="running",
        attempt_count=1,
        max_attempts=1,
        worker_id="dead",
        started_at=datetime.now(UTC) - timedelta(minutes=2),
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    async with sessions() as session:
        session.add(job)
        await session.commit()
    stream = run_events.subscribe(owner.id, conversation.id, run.id)
    assert await anext(stream) == ": heartbeat\n\n"
    try:
        await PostgresJobQueue().recover_expired_leases(queue_name="notify-qa")
        packet = await asyncio.wait_for(anext(stream), 1)
        assert "event: error" in packet
    finally:
        await stream.aclose()
    assert not hub.events and hub.connection is None
