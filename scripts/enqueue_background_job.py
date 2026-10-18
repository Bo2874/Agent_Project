"""Enqueue a registered maintenance background job."""

import argparse
import asyncio
import sys

from backend.background import PostgresJobQueue, build_background_job_registry
from backend.core.config import get_settings
from backend.database import engine


async def enqueue(job_type: str, *, idempotency_key: str | None, max_attempts: int) -> None:
    settings = get_settings()
    job = await PostgresJobQueue().enqueue(
        job_type=job_type,
        queue_name=settings.background_queue_name,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
    )
    await engine.dispose()
    print(f"Đã enqueue job {job.id} ({job.job_type}) vào queue '{job.queue_name}'.")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    registry = build_background_job_registry()
    parser = argparse.ArgumentParser(description="Thêm maintenance job vào background queue.")
    parser.add_argument("job_type", choices=registry.names())
    parser.add_argument("--idempotency-key")
    parser.add_argument("--max-attempts", type=int, default=1, choices=range(1, 6))
    args = parser.parse_args()
    asyncio.run(
        enqueue(
            args.job_type,
            idempotency_key=args.idempotency_key,
            max_attempts=args.max_attempts,
        )
    )


if __name__ == "__main__":
    main()
