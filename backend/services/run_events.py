"""Owner-scoped SSE replay and fenced operation journal, without a second scheduler."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from sqlalchemy import func, select

from agent_core.graph.runtime import AgentNodeExecutionError
from backend.background.queue import BackgroundJobOwnershipError
from backend.database import SessionFactory
from backend.models import AgentOperation, AgentRun, BackgroundJob, Conversation, RunEvent, User

current_job = ContextVar("agent_job_lease", default=None)


async def fence(session, *, allow_cancel=False):
    claimed = current_job.get()
    if claimed is None:
        return
    job = await session.scalar(
        select(BackgroundJob).where(BackgroundJob.id == claimed.id).with_for_update()
    )
    if (
        job is None
        or job.status != "running"
        or job.worker_id != claimed.worker_id
        or job.attempt_count != claimed.attempt_count
        or job.started_at != claimed.started_at
        or job.lease_expires_at is None
        or job.lease_expires_at <= datetime.now(UTC)
        or (job.cancel_requested_at is not None and not allow_cancel)
    ):
        raise BackgroundJobOwnershipError("Agent worker lease no longer valid")
    if job.user_id is not None and not allow_cancel:
        owner = await session.get(User, job.user_id)
        if owner is None or not owner.is_active:
            raise BackgroundJobOwnershipError("Run owner no longer active")


@asynccontextmanager
async def runtime_session(*, allow_cancel=False):
    async with SessionFactory() as session:
        await fence(session, allow_cancel=allow_cancel)
        yield session


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


class OperationJournal:
    def __init__(self, run_id):
        self.run_id = run_id

    async def load(self, key, digest):
        async with runtime_session() as session:
            row = await session.scalar(
                select(AgentOperation).where(
                    AgentOperation.agent_run_id == self.run_id, AgentOperation.key == key
                )
            )
            if row is None:
                return None
            if row.fingerprint != digest or row.status != "completed":
                raise AgentNodeExecutionError(
                    "Lời gọi trước chưa rõ kết quả hoặc đầu vào thay đổi; không tự gọi lại.",
                    code="agent_operation_unknown",
                )
            return row.result

    async def begin(self, key, digest):
        async with runtime_session() as session:
            session.add(
                AgentOperation(
                    agent_run_id=self.run_id, key=key, fingerprint=digest, status="started"
                )
            )
            await session.commit()

    async def finish(self, key, result, tokens):
        async with runtime_session() as session:
            row = await session.scalar(
                select(AgentOperation)
                .where(AgentOperation.agent_run_id == self.run_id, AgentOperation.key == key)
                .with_for_update()
            )
            row.status, row.result, row.actual_tokens = "completed", result, tokens
            await session.commit()

    async def total_spent(self):
        async with runtime_session() as session:
            return await session.scalar(
                select(func.coalesce(func.sum(AgentOperation.actual_tokens), 0)).where(
                    AgentOperation.agent_run_id == self.run_id
                )
            )


async def append_event(run_id, kind, data, *, allow_cancel=False):
    async with runtime_session(allow_cancel=allow_cancel) as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None:
            return
        run.event_sequence += 1
        session.add(
            RunEvent(agent_run_id=run_id, sequence=run.event_sequence, kind=kind, data=data)
        )
        await session.commit()


async def append_packet(run_id, packet):
    kind, data = "message", []
    for line in packet.splitlines():
        if line.startswith("event:"):
            kind = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
    if data:
        await append_event(run_id, kind, json.loads("\n".join(data)))


def event_cursor(run_id, value):
    if not value:
        return 0
    prefix, separator, sequence = value.rpartition(":")
    if not separator or prefix != str(run_id) or not sequence.isdigit() or len(sequence) > 12:
        raise ValueError("Last-Event-ID không hợp lệ")
    return int(sequence)


async def subscribe(owner, conversation_id, run_id, after=0):
    """Subscriber cancellation never touches the durable job."""
    first_page = True
    while True:
        async with SessionFactory() as session:
            run = await session.scalar(
                select(AgentRun)
                .join(Conversation)
                .join(User)
                .where(
                    AgentRun.id == run_id,
                    Conversation.id == conversation_id,
                    User.id == owner,
                    User.is_active,
                )
            )
            if run is None:
                return
            if first_page and after == 0:
                queued = await session.scalar(
                    select(func.max(RunEvent.sequence)).where(
                        RunEvent.agent_run_id == run_id, RunEvent.kind == "queued"
                    )
                )
                after = max(0, (queued or 1) - 1)
            first_page = False
            events = (
                await session.scalars(
                    select(RunEvent)
                    .where(RunEvent.agent_run_id == run_id, RunEvent.sequence > after)
                    .order_by(RunEvent.sequence)
                    .limit(100)
                )
            ).all()
            terminal = run.status in {"completed", "failed", "cancelled"}
            active = await session.scalar(
                select(BackgroundJob.id)
                .where(
                    BackgroundJob.agent_run_id == run_id,
                    BackgroundJob.status.in_(["queued", "running"]),
                )
                .limit(1)
            )
        for event in events:
            after = event.sequence
            yield (
                f"id: {run_id}:{event.sequence}\nevent: {event.kind}\n"
                f"data: {json.dumps(event.data, ensure_ascii=False)}\n\n"
            )
            if event.kind in {"done", "error", "cancelled"}:
                return
        if terminal and not events and not active:
            # Terminal DB state can be committed before the final stream packet
            # if a worker died during persistence. Reload the authoritative answer.
            yield (
                "event: snapshot\ndata: "
                + json.dumps(
                    {
                        "status": run.status,
                        "message_id": str(run.assistant_message_id)
                        if run.assistant_message_id
                        else None,
                    }
                )
                + "\n\n"
            )
            return
        if not events:
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)
