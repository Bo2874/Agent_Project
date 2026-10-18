"""Smoke-test enqueue, exclusive claim and completion against PostgreSQL."""

import asyncio
from uuid import uuid4

from sqlalchemy import delete

from backend.background import (
    BackgroundJobRegistry,
    BackgroundWorker,
    JobExecutionContext,
    PostgresJobQueue,
)
from backend.database import SessionFactory, engine
from backend.models import BackgroundJob


async def echo_handler(_: JobExecutionContext, payload: dict[str, object]) -> dict[str, object]:
    return {"echo": payload.get("text")}


async def run_check() -> int:
    queue_name = f"check-{uuid4().hex[:12]}"
    queue = PostgresJobQueue()
    registry = BackgroundJobRegistry()
    registry.register("test.echo", echo_handler)
    worker = BackgroundWorker(
        queue=queue,
        registry=registry,
        queue_name=queue_name,
        worker_id="check-worker",
        poll_seconds=0.1,
    )
    created_ids = []
    try:
        completed_job = await queue.enqueue(
            job_type="test.echo",
            payload={"text": "xin chào"},
            queue_name=queue_name,
        )
        created_ids.append(completed_job.id)
        processed = await worker.run_once()
        completed = await queue.get(completed_job.id)

        claimed_job = await queue.enqueue(
            job_type="test.echo",
            payload={"text": "claim once"},
            queue_name=queue_name,
        )
        created_ids.append(claimed_job.id)
        claims = await asyncio.gather(
            queue.claim_next(queue_name=queue_name, worker_id="claim-worker-a"),
            queue.claim_next(queue_name=queue_name, worker_id="claim-worker-b"),
        )
        successful_claims = [claim for claim in claims if claim is not None]
        if successful_claims:
            await queue.succeed(successful_claims[0], {"claimed_once": True})

        checks = {
            "enqueue_and_process": processed,
            "completed_status": completed is not None and completed.status == "succeeded",
            "result_persisted": completed is not None and completed.result == {"echo": "xin chào"},
            "single_consumer_claim": len(successful_claims) == 1,
        }
        for name, passed in checks.items():
            print(f"{name}: {'PASSED' if passed else 'FAILED'}")
        all_passed = all(checks.values())
        print("BACKGROUND_QUEUE_PASSED" if all_passed else "BACKGROUND_QUEUE_FAILED")
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
