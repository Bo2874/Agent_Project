"""Request cancellation of a queued or running background job."""

import argparse
import asyncio
import sys
from uuid import UUID

from backend.background import PostgresJobQueue
from backend.database import engine


async def cancel(job_id: UUID) -> None:
    try:
        status = await PostgresJobQueue().request_cancel(job_id)
        print(f"Job {job_id}: {status}")
    finally:
        await engine.dispose()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Yêu cầu hủy background job.")
    parser.add_argument("job_id", type=UUID)
    args = parser.parse_args()
    asyncio.run(cancel(args.job_id))


if __name__ == "__main__":
    main()
