import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from backend.background import (
    BackgroundJobRegistry,
    BackgroundWorker,
    ClaimedJob,
    JobExecutionContext,
    PostgresJobQueue,
    RetryableJobError,
)
from backend.models import BackgroundJob

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeQueue:
    def __init__(self, job: ClaimedJob | None) -> None:
        self.job = job
        self.succeeded: list[tuple[ClaimedJob, dict[str, Any]]] = []
        self.failed: list[tuple[ClaimedJob, str, str]] = []
        self.retried: list[tuple[ClaimedJob, str, float]] = []
        self.cancelled: list[ClaimedJob] = []
        self.cancel_requested = False
        self.recovered = 0

    async def recover_expired_leases(self, *, queue_name: str) -> int:
        assert queue_name == "default"
        self.recovered += 1
        return 0

    async def claim_next(
        self, *, queue_name: str, worker_id: str, lease_seconds: float
    ) -> ClaimedJob | None:
        assert queue_name == "default"
        assert worker_id == "worker-1"
        assert lease_seconds == 2
        job, self.job = self.job, None
        return job

    async def is_cancel_requested(self, _: ClaimedJob) -> bool:
        return self.cancel_requested

    async def renew_lease(self, _: ClaimedJob, *, lease_seconds: float) -> None:
        assert lease_seconds == 2

    async def cancel_running(self, job: ClaimedJob) -> None:
        self.cancelled.append(job)

    async def succeed(self, job: ClaimedJob, result: dict[str, Any]) -> None:
        self.succeeded.append((job, result))

    async def fail(
        self,
        job: ClaimedJob,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self.failed.append((job, error_code, error_message))

    async def retry_or_fail(
        self,
        job: ClaimedJob,
        *,
        error_code: str,
        error_message: str,
        delay_seconds: float,
    ) -> str:
        del error_message
        self.retried.append((job, error_code, delay_seconds))
        return "queued"


def make_job(job_type: str = "test.echo") -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        queue_name="default",
        job_type=job_type,
        payload={"text": "hello"},
        worker_id="worker-1",
        started_at=datetime.now(UTC),
        attempt_count=1,
        max_attempts=3,
    )


def make_worker(queue: FakeQueue, registry: BackgroundJobRegistry) -> BackgroundWorker:
    return BackgroundWorker(
        queue=queue,  # type: ignore[arg-type]
        registry=registry,
        queue_name="default",
        worker_id="worker-1",
        poll_seconds=0.1,
        lease_seconds=2,
        control_poll_seconds=0.05,
        retry_base_seconds=0,
        retry_max_seconds=0,
    )


def test_background_job_model_has_durable_status_and_claim_index() -> None:
    table = BackgroundJob.__table__
    status_constraint = next(
        item for item in table.constraints if item.name == "ck_background_jobs_status"
    )

    assert {column.name for column in table.primary_key.columns} == {"id"}
    assert all(
        status in str(status_constraint.sqltext)
        for status in ("queued", "running", "succeeded", "failed", "cancelled")
    )
    index_names = {index.name for index in table.indexes}
    assert "ix_background_jobs_claim" in index_names
    assert "uq_background_jobs_idempotency" in index_names
    assert "ix_background_jobs_lease_recovery" in index_names
    assert {"attempt_count", "max_attempts", "cancel_requested_at"} <= {
        column.name for column in table.columns
    }


def test_claim_query_uses_skip_locked_and_deterministic_priority() -> None:
    statement = PostgresJobQueue.build_claim_statement(
        queue_name="default",
        now=datetime.now(UTC),
    )
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY BACKGROUND_JOBS.PRIORITY DESC" in sql
    assert "LIMIT" in sql


def test_migration_declares_background_jobs_and_current_revision() -> None:
    migration = (
        PROJECT_ROOT / "migrations/versions/20260928_0016_create_background_jobs.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "20260928_0016"' in migration
    assert 'down_revision: str | Sequence[str] | None = "20260917_0015"' in migration
    assert '"background_jobs"' in migration
    assert '"ix_background_jobs_claim"' in migration

    reliability_migration = (
        PROJECT_ROOT / "migrations/versions/20260928_0017_add_background_job_reliability.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "20260928_0017"' in reliability_migration
    assert 'down_revision: str | Sequence[str] | None = "20260928_0016"' in reliability_migration
    assert '"uq_background_jobs_idempotency"' in reliability_migration


def test_registry_rejects_duplicate_handler() -> None:
    registry = BackgroundJobRegistry()

    async def handler(_: JobExecutionContext, __: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    registry.register("test.echo", handler)
    with pytest.raises(ValueError, match="đã tồn tại"):
        registry.register("test.echo", handler)


@pytest.mark.asyncio
async def test_worker_completes_claimed_job() -> None:
    queue = FakeQueue(make_job())
    registry = BackgroundJobRegistry()

    async def handler(_: JobExecutionContext, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"echo": payload["text"]}

    registry.register("test.echo", handler)

    assert await make_worker(queue, registry).run_once() is True
    assert queue.succeeded[0][1] == {"echo": "hello"}
    assert queue.failed == []
    assert queue.recovered == 1


@pytest.mark.asyncio
async def test_worker_marks_unknown_job_failed() -> None:
    queue = FakeQueue(make_job("missing.handler"))

    assert await make_worker(queue, BackgroundJobRegistry()).run_once() is True
    assert queue.succeeded == []
    assert queue.failed[0][1] == "unknown_job_type"


@pytest.mark.asyncio
async def test_worker_contains_handler_exception() -> None:
    queue = FakeQueue(make_job())
    registry = BackgroundJobRegistry()

    async def broken_handler(_: JobExecutionContext, __: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError("sensitive internal details")

    registry.register("test.echo", broken_handler)

    assert await make_worker(queue, registry).run_once() is True
    assert queue.failed[0][1] == "job_handler_failed"
    assert "sensitive internal details" not in queue.failed[0][2]


@pytest.mark.asyncio
async def test_worker_only_retries_explicit_transient_failure() -> None:
    queue = FakeQueue(make_job())
    registry = BackgroundJobRegistry()

    async def transient_handler(_: JobExecutionContext, __: Mapping[str, Any]) -> dict[str, Any]:
        raise RetryableJobError("upstream unavailable", error_code="upstream_unavailable")

    registry.register("test.echo", transient_handler)

    assert await make_worker(queue, registry).run_once() is True
    assert queue.retried[0][1:] == ("upstream_unavailable", 0)
    assert queue.failed == []


@pytest.mark.asyncio
async def test_worker_cooperatively_cancels_running_handler() -> None:
    queue = FakeQueue(make_job())
    queue.cancel_requested = True
    registry = BackgroundJobRegistry()

    async def slow_handler(_: JobExecutionContext, __: Mapping[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(10)
        return {}

    registry.register("test.echo", slow_handler)

    assert await make_worker(queue, registry).run_once() is True
    assert len(queue.cancelled) == 1
    assert queue.succeeded == []


def test_retry_delay_is_exponential_and_capped() -> None:
    queue = FakeQueue(None)
    worker = BackgroundWorker(
        queue=queue,  # type: ignore[arg-type]
        registry=BackgroundJobRegistry(),
        queue_name="default",
        worker_id="worker-1",
        lease_seconds=2,
        control_poll_seconds=0.05,
        retry_base_seconds=2,
        retry_max_seconds=5,
    )

    assert [worker.retry_delay(attempt) for attempt in (1, 2, 3, 4)] == [2, 4, 5, 5]
