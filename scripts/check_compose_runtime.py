"""Offline integration probe for a fresh Compose dev database, run inside the app image."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select, text

from agent_core.graph import AgentLoopEvent, stream_agent_graph
from agent_core.graph.checkpointing import agent_checkpoint_config, agent_thread_id
from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.tools import ToolContext
from backend.background import PostgresJobQueue
from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.dependencies_tools import (
    close_tool_connections,
    get_tool_registry,
    start_tool_connections,
)
from backend.models import BackgroundJob, User
from backend.services.langgraph_checkpoints import (
    close_langgraph_checkpoints,
    get_langgraph_checkpointer,
    start_langgraph_checkpoints,
)


class ProbeModel:
    """Deterministic fixture, never registered as an application model provider."""

    provider = "fixture"
    model = "compose-mcp-fixture"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages: list, tools=None, user_id=None) -> AsyncIterator[LLMChunk]:
        del messages, user_id
        self.calls += 1
        if self.calls == 1:
            assert any(t["function"]["name"] == "demo__calculate" for t in tools)
            yield LLMChunk(
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0,
                        id="compose-calculate",
                        name="demo__calculate",
                        arguments=json.dumps({"a": 2, "b": 3, "operation": "multiply"}),
                    ),
                )
            )
            yield LLMChunk(usage=TokenUsage(1, 1, 2), finish_reason="tool_calls")
        else:
            assert self.calls == 2
            yield LLMChunk(content="6")
            yield LLMChunk(usage=TokenUsage(1, 1, 2), finish_reason="stop")

    async def aclose(self) -> None:
        pass


async def run() -> dict:
    settings = get_settings()
    if settings.app_env != "development" or settings.db_host != "postgres":
        raise RuntimeError("This probe only runs against the Compose dev database")
    async with SessionFactory() as session:
        if await session.scalar(select(func.count(User.id))):
            raise RuntimeError("Use a fresh dev database with no users for this startup probe")
        version = await session.scalar(text("SELECT version_num FROM alembic_version"))
        vector_version = await session.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        )
        distance = await session.scalar(text("SELECT '[1,0,0]'::vector <-> '[0,1,0]'::vector"))
        assert version and vector_version and abs(float(distance) - 2**0.5) < 1e-6

    queue = PostgresJobQueue()
    job = await queue.enqueue(
        job_type="maintenance.purge_expired_memories",
        queue_name=settings.background_queue_name,
    )
    try:
        for _ in range(100):
            current = await queue.get(job.id)
            if current.status in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.2)
        assert current.status == "succeeded", "The separate worker did not complete the probe job"
    finally:
        async with SessionFactory() as session:
            await session.execute(delete(BackgroundJob).where(BackgroundJob.id == job.id))
            await session.commit()

    run_id = uuid4()
    completed = []

    async def record(event: AgentLoopEvent) -> None:
        if event.kind == "tool_completed":
            completed.append(event)

    await start_langgraph_checkpoints()
    saver = get_langgraph_checkpointer()
    try:
        await start_tool_connections()
        assert "run_python_code" not in get_tool_registry()
        model = ProbeModel()
        chunks = [
            item
            async for item in stream_agent_graph(
                model=model,
                messages=[{"role": "user", "content": "calculate 2 multiply 3"}],
                registry=get_tool_registry(),
                context=ToolContext(
                    user_id=uuid4(),
                    conversation_id=uuid4(),
                    user_role="user",
                    run_id=run_id,
                ),
                event_handler=record,
                graph_checkpointer=saver,
            )
        ]
        assert model.calls == 2 and len(completed) == 1
        assert completed[0].tool_result.data["result"] == 6
        assert any(isinstance(item, LLMChunk) and item.content == "6" for item in chunks)
        assert await saver.aget_tuple(agent_checkpoint_config(run_id)) is not None
    finally:
        await close_tool_connections()
        await saver.adelete_thread(agent_thread_id(run_id))
        await close_langgraph_checkpoints()

    assert os.getuid() == 10001
    assert not Path("/app/.env").exists() and not Path("/app/.venv").exists()
    assert not Path("/var/run/docker.sock").exists()
    assert not any(
        settings.v2_features.is_enabled(name)
        for name in (
            "plugins",
            "routing",
            "rag",
            "planning",
            "parallel",
            "redis_cache",
            "events",
        )
    )
    assert not settings.langgraph_checkpoint_auto_setup
    probe = Path("/app/data/documents/.compose-startup-probe.txt")
    probe.write_text("compose-volume-persistence-v1", encoding="utf-8")
    return {
        "alembic_revision": version,
        "pgvector_version": vector_version,
        "vector_distance": float(distance),
        "worker_consumed_job": True,
        "graph_mcp_calculation": 6,
        "postgres_checkpoint": True,
        "uid": os.getuid(),
        "no_host_env_or_virtualenv": True,
        "no_docker_socket": True,
        "v2_disabled": True,
        "checkpoint_runtime_ddl_disabled": True,
        "document_probe": str(probe),
        "llm_provider_calls": 0,
    }


async def main() -> None:
    try:
        print(json.dumps(await run(), ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
