"""Smoke-test cancellation, retry, idempotency and lease recovery in PostgreSQL."""

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from sqlalchemy import delete

from backend.background import (
    BackgroundIdempotencyConflictError,
    BackgroundJobRegistry,
    BackgroundWorker,
    JobExecutionContext,
    PostgresJobQueue,
    RetryableJobError,
)
from backend.database import SessionFactory, engine
from backend.models import BackgroundJob


async def wait_for_status(
    queue: PostgresJobQueue,
    job_id: Any,
    expected: str,
    *,
    timeout: float = 2,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        job = await queue.get(job_id)
        if job is not None and job.status == expected:
            return True
        await asyncio.sleep(0.02)
    return False


async def run_check() -> int:
    base_name = f"rel-{uuid4().hex[:10]}"
    idempotency_queue = f"{base_name}-idem"
    retry_queue = f"{base_name}-retry"
    cancel_queue = f"{base_name}-cancel"
    lease_queue = f"{base_name}-lease"
    queue = PostgresJobQueue()
    created_ids = []
    checks: dict[str, bool] = {}
    try:
        idempotency_key = f"smoke:{uuid4().hex}"
        first = await queue.enqueue(
            job_type="test.echo",
            payload={"text": "same"},
            queue_name=idempotency_queue,
            idempotency_key=idempotency_key,
        )
        created_ids.append(first.id)
        duplicate = await queue.enqueue(
            job_type="test.echo",
            payload={"text": "same"},
            queue_name=idempotency_queue,
            idempotency_key=idempotency_key,
        )
        checks["idempotency_reuses_job"] = duplicate.id == first.id
        try:
            await queue.enqueue(
                job_type="test.echo",
                payload={"text": "different"},
                queue_name=idempotency_queue,
                idempotency_key=idempotency_key,
            )
        except BackgroundIdempotencyConflictError:
            checks["idempotency_conflict"] = True
        else:
            checks["idempotency_conflict"] = False

        retry_job = await queue.enqueue(
            job_type="test.retry_once",
            queue_name=retry_queue,
            max_attempts=2,
        )
        created_ids.append(retry_job.id)
        attempts = 0

        async def retry_once(_: JobExecutionContext, __: Mapping[str, Any]) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RetryableJobError("temporary", error_code="temporary_failure")
            return {"attempts": attempts}

        registry = BackgroundJobRegistry()
        registry.register("test.retry_once", retry_once)
        worker = BackgroundWorker(
            queue=queue,
            registry=registry,
            queue_name=retry_queue,
            worker_id="reliability-worker",
            poll_seconds=0.05,
            lease_seconds=2,
            control_poll_seconds=0.05,
            retry_base_seconds=0,
            retry_max_seconds=0,
        )
        await worker.run_once()
        after_first = await queue.get(retry_job.id)
        await worker.run_once()
        after_second = await queue.get(retry_job.id)
        checks["controlled_retry"] = (
            after_first is not None
            and after_first.status == "queued"
            and after_second is not None
            and after_second.status == "succeeded"
            and after_second.attempt_count == 2
        )

        queued_job = await queue.enqueue(
            job_type="test.never_runs",
            queue_name=cancel_queue,
        )
        created_ids.append(queued_job.id)
        await queue.request_cancel(queued_job.id)
        cancelled_queued = await queue.get(queued_job.id)
        checks["cancel_queued"] = (
            cancelled_queued is not None and cancelled_queued.status == "cancelled"
        )

        running_job = await queue.enqueue(
            job_type="test.slow",
            queue_name=cancel_queue,
        )
        created_ids.append(running_job.id)

        async def slow(_: JobExecutionContext, __: Mapping[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(10)
            return {}

        running_registry = BackgroundJobRegistry()
        running_registry.register("test.slow", slow)
        running_worker = BackgroundWorker(
            queue=queue,
            registry=running_registry,
            queue_name=cancel_queue,
            worker_id="cancel-worker",
            poll_seconds=0.05,
            lease_seconds=2,
            control_poll_seconds=0.05,
        )
        worker_task = asyncio.create_task(running_worker.run_once())
        became_running = await wait_for_status(queue, running_job.id, "running")
        if became_running:
            await queue.request_cancel(running_job.id)
        await worker_task
        cancelled_running = await queue.get(running_job.id)
        checks["cancel_running"] = (
            became_running
            and cancelled_running is not None
            and cancelled_running.status == "cancelled"
        )

        lease_job = await queue.enqueue(
            job_type="test.lease",
            queue_name=lease_queue,
            max_attempts=2,
        )
        created_ids.append(lease_job.id)
        claimed = await queue.claim_next(
            queue_name=lease_queue,
            worker_id="dead-worker",
            lease_seconds=0.05,
        )
        await asyncio.sleep(0.08)
        recovered = await queue.recover_expired_leases(queue_name=lease_queue)
        lease_after = await queue.get(lease_job.id)
        checks["expired_lease_requeued"] = (
            claimed is not None
            and recovered == 1
            and lease_after is not None
            and lease_after.status == "queued"
        )

        for name, passed in checks.items():
            print(f"{name}: {'PASSED' if passed else 'FAILED'}")
        all_passed = all(checks.values())
        print("BACKGROUND_RELIABILITY_PASSED" if all_passed else "BACKGROUND_RELIABILITY_FAILED")
        return 0 if all_passed else 1
    finally:
        if created_ids:
            async with SessionFactory() as session:
                await session.execute(
                    delete(BackgroundJob).where(BackgroundJob.id.in_(created_ids))
                )
                await session.commit()
        await engine.dispose()


def main() -> None:
    raise SystemExit(asyncio.run(run_check()))


if __name__ == "__main__":
    main()
