"""Loopback-only browser fixture. Real app/queue/DB; fake auth and text model.

Never an application entry point. Requires a dedicated *_test DB and an
already-created test_workspace_<uuid> schema owned by the parent test.
"""

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = make_url(os.environ["GOOGLE_TEST_DATABASE_URL"])
    schema = os.environ["AUTOMATION_QA_SCHEMA"]
    owner = UUID(os.environ["AUTOMATION_QA_OWNER"])
    if not url.database.endswith("_test") or not re.fullmatch(
        r"test_workspace_[a-f0-9]{32}", schema
    ):
        raise RuntimeError("Browser fixture requires an isolated test database and schema")
    import backend.core.config as config

    settings = config.Settings(
        _env_file=None,
        db_host=url.host,
        db_port=url.port,
        db_name=url.database,
        db_user=url.username,
        db_password=url.password,
        auth_secret_key="automation-browser-fixture-" * 2,
        automations_enabled=True,
        agent_react_enabled=True,
        memory_extraction_enabled=False,
        deepseek_model="automation-web-fixture",
        deepseek_max_tokens=128,
    )
    config.get_settings = lambda: settings
    import backend.database as database

    database.engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    database.SessionFactory = async_sessionmaker(database.engine, expire_on_commit=False)
    from langgraph.checkpoint.memory import InMemorySaver

    import backend.dependencies_llm as llm
    import backend.dependencies_skills as skills
    import backend.dependencies_tools as tools
    from agent_core.llm.types import LLMChunk, TokenUsage
    from agent_core.skills import SkillRegistry
    from agent_core.tools import ToolRegistry
    from backend.services import langgraph_checkpoints

    class TextModel:
        provider, model, max_tokens = "fixture", settings.deepseek_model, 128

        async def stream(self, messages, **kwargs):
            slow = any("SLOW" in (message.get("content") or "") for message in messages)
            await asyncio.sleep(30 if slow else 2)
            yield LLMChunk(
                content="Báo cáo kiểm thử đã hoàn tất. Dữ liệu này là fixture offline.",
                finish_reason="stop",
                usage=TokenUsage(total_tokens=30_000),
            )

        async def aclose(self):
            pass

    llm.create_chat_model = lambda **kwargs: TextModel()
    tools.get_tool_registry = ToolRegistry
    skills.get_skill_registry = SkillRegistry
    saver = InMemorySaver()
    langgraph_checkpoints.get_langgraph_checkpointer = lambda: saver
    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.dependencies import get_optional_user
    from backend.main import app
    from backend.models import User
    from backend.services.langsmith_tracing import close_langsmith_tracing, start_langsmith_tracing

    async def fixture_owner():
        async with database.SessionFactory() as session:
            return await session.get(User, owner)

    app.dependency_overrides[get_optional_user] = fixture_owner

    @asynccontextmanager
    async def lifespan(_):
        start_langsmith_tracing()
        stop = asyncio.Event()
        worker = BackgroundWorker(
            queue=PostgresJobQueue(),
            registry=build_background_job_registry(),
            queue_name="default",
            worker_id="browser-fixture",
            poll_seconds=0.1,
            control_poll_seconds=0.1,
        )
        task = asyncio.create_task(worker.run_forever(stop))
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await close_langsmith_tracing()
            await database.engine.dispose()

    app.router.lifespan_context = lifespan
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ["AUTOMATION_QA_PORT"]), log_level="warning"
    )


if __name__ == "__main__":
    main()
