"""Task-owned subprocess for hard-crash/restart acceptance, never a production worker."""

import asyncio
import os
from pathlib import Path


async def main():
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.core.config import Settings
    from backend.services.artifacts.runner_client import ArtifactRunnerClient
    from backend.services.artifacts.service import ArtifactService
    from backend.services.artifacts.storage import ArtifactStorage
    from backend.services.artifacts.tools import ArtifactToolService
    from scripts.artifact_fixture import OfflineArtifactRuntime

    url = make_url(os.environ["ARTIFACT_TEST_DATABASE_URL"])
    if not url.database.endswith("_test"):
        raise ValueError("Fixture worker requires an explicitly named test database")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        db_host=url.host,
        db_port=url.port,
        db_name=url.database,
        db_user=url.username,
        db_password=url.password,
        auth_secret_key="fixture-auth-key-" * 3,
        workspace_artifacts_enabled=True,
        memory_extraction_enabled=False,
        agent_react_enabled=True,
        background_queue_name=os.environ["ARTIFACT_FIXTURE_QUEUE"],
        artifact_storage_path=Path(os.environ["ARTIFACT_FIXTURE_STORAGE"]),
    )
    storage = ArtifactService(ArtifactStorage(settings.artifact_storage_path))
    if marker := os.environ.get("ARTIFACT_FIXTURE_CRASH_MARKER"):
        original = ArtifactToolService.execute

        async def crash(self, *args, **kwargs):
            result = await original(self, *args, **kwargs)
            if not result.is_error:
                Path(marker).write_text("published before journal receipt", encoding="utf-8")
                os._exit(86)
            return result

        ArtifactToolService.execute = crash
    runtime = await OfflineArtifactRuntime(
        settings,
        sessions,
        storage,
        runner_factory=lambda owner: ArtifactRunnerClient(
            os.environ["ARTIFACT_GATE_RUNNER_URL"],
            key=os.environ["ARTIFACT_GATE_RUNNER_KEY"],
            storage=storage.storage,
            owner=owner,
        ),
    ).start()
    try:
        if not await runtime.worker.run_once():
            raise RuntimeError("No owned fixture job available")
    finally:
        await runtime.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
