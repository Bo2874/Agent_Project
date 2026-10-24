"""Dev-only acceptance probe, piped into the app container with python - MODE."""

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import delete, select

from backend.background import PostgresJobQueue
from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.models import BackgroundJob

QUEUE = "compose-startup-probe"
KEY = "day78-volume-persistence-v1"
DOCUMENT = Path("/app/data/documents/.compose-startup-probe.txt")


async def main():
    settings = get_settings()
    assert settings.app_env == "development" and settings.db_host == "postgres"
    mode = sys.argv[1]
    assert mode in {"seed", "check", "cleanup"}
    try:
        if mode == "seed":
            job = await PostgresJobQueue().enqueue(
                job_type="test.persistence",
                queue_name=QUEUE,
                idempotency_key=KEY,
                payload={"sentinel": KEY},
            )
            DOCUMENT.write_text("compose-volume-persistence-v1", encoding="utf-8")
        else:
            async with SessionFactory() as session:
                job = await session.scalar(
                    select(BackgroundJob).where(
                        BackgroundJob.queue_name == QUEUE,
                        BackgroundJob.idempotency_key == KEY,
                    )
                )
                assert job is not None and job.payload == {"sentinel": KEY}
                assert job.status == "queued"
                assert DOCUMENT.read_text(encoding="utf-8") == "compose-volume-persistence-v1"
                if mode == "cleanup":
                    await session.execute(delete(BackgroundJob).where(BackgroundJob.id == job.id))
                    await session.commit()
                    DOCUMENT.unlink()
        print(json.dumps({"mode": mode, "job_id": str(job.id), "database": True, "document": True}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
