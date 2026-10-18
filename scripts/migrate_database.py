"""One-shot container migration: vector extension, Alembic and LangGraph schema."""

import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from backend.core.config import PROJECT_ROOT, get_settings
from backend.core.logging import install_log_redaction
from backend.services.langgraph_checkpoints import LangGraphCheckpointService


async def setup_checkpoints() -> None:
    settings = get_settings()
    service = LangGraphCheckpointService(
        conninfo=settings.langgraph_checkpoint_database_uri,
        min_size=1,
        max_size=1,
        setup_schema=True,
    )
    try:
        await service.start()
    finally:
        await service.close()


def main() -> None:
    install_log_redaction()
    settings = get_settings()
    engine = create_engine(settings.migration_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    finally:
        engine.dispose()
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    asyncio.run(setup_checkpoints())
    print("CONTAINER_MIGRATIONS_COMPLETED")


if __name__ == "__main__":
    main()
