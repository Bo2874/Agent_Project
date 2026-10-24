"""Offline HTTP/SSE/PostgreSQL routing probe; only for a disposable Compose dev DB."""

import asyncio
import json
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, func, select

from agent_core.contracts import FeatureFlags, RoutingDecision
from agent_core.graph import stream_agent_graph
from agent_core.graph.checkpointing import agent_checkpoint_config, agent_thread_id
from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.skills import SkillRegistry
from agent_core.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    create_context_tool,
)
from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.dependencies_llm import get_chat_model
from backend.dependencies_skills import get_skill_registry
from backend.dependencies_tools import get_tool_registry
from backend.main import app
from backend.models import AgentRun, Message, ModelCall, User
from backend.services.langgraph_checkpoints import (
    close_langgraph_checkpoints,
    get_langgraph_checkpointer,
    start_langgraph_checkpoints,
)
from backend.services.langsmith_tracing import close_langsmith_tracing, start_langsmith_tracing
from scripts.auth_probe_diagnostics import auth_diagnostics


class Model:
    provider, model = "fixture", "query-routing-offline"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.requests = []
        self.closed = False

    async def stream(self, messages, tools=None, user_id=None):
        self.requests.append(deepcopy(tools))
        if self.fail:
            raise RuntimeError("synthetic interrupted route")
        query = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        if "Calculate" in query and len(self.requests) == 1:
            yield LLMChunk(
                tool_call_deltas=(
                    LLMToolCallDelta(index=0, id="route-call", name="calculator", arguments="{}"),
                ),
                finish_reason="tool_calls",
            )
        else:
            yield LLMChunk(
                content="Synthetic answer.", finish_reason="stop", usage=TokenUsage(1, 1, 2)
            )

    async def aclose(self):
        self.closed = True


def events(text):
    return [
        (lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: ")))
        for block in text.strip().split("\n\n")
        if (lines := block.splitlines())
    ]


async def check():
    settings = get_settings()
    if (
        settings.app_env != "development"
        or settings.db_host != "postgres"
        or not settings.v2_features.is_enabled("routing")
        or settings.v2_features.is_enabled("plugins")
        or settings.memory_extraction_enabled
        or settings.langsmith_tracing_enabled
    ):
        raise RuntimeError("Use isolated Compose dev DB, routing on, plugins/memory/tracing off")
    old_overrides = dict(app.dependency_overrides)
    old_master = settings.v2_enabled
    users, threads, models, calls = [], [], [], []
    suffix = uuid4().hex
    registry = ToolRegistry()

    async def calculate(context, arguments):
        calls.append(context.run_id)
        return ToolResult.success("4")

    registry.register(
        create_context_tool(
            name="calculator",
            description="Calculate exact numbers",
            args_schema={"type": "object", "properties": {}},
            coroutine=calculate,
        ),
        routing=ToolRoutingPolicy(keywords=frozenset({"calculate", "tính"})),
    )

    async def model_dependency():
        model = Model()
        models.append(model)
        try:
            yield model
        finally:
            await model.aclose()

    start_langsmith_tracing()
    await start_langgraph_checkpoints()
    saver = get_langgraph_checkpointer()
    try:
        app.dependency_overrides[get_chat_model] = model_dependency
        app.dependency_overrides[get_tool_registry] = lambda: registry
        app.dependency_overrides[get_skill_registry] = SkillRegistry
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://probe") as owner,
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://probe") as other,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://probe"
            ) as anonymous,
        ):

            async def request(client, method, path, body=None, expected=200):
                response = await client.request(method, path, **({"json": body} if body else {}))
                diagnostics = (
                    await auth_diagnostics(client, response, settings)
                    if response.status_code == 401 and expected != 401
                    else {}
                )
                assert response.status_code == expected, (
                    f"Routing probe {method} {path}: {response.status_code}; {diagnostics}"
                )
                return response

            for label, client in (("owner", owner), ("other", other)):
                response = await request(
                    client,
                    "POST",
                    "/api/auth/register",
                    {
                        "email": f"routing-{label}-{suffix}@example.com",
                        "password": f"Fixture@{suffix}",
                        "full_name": "Synthetic routing user",
                    },
                    201,
                )
                users.append(UUID(response.json()["user"]["id"]))
            for query, expected_route in (
                ("Hello", "chat"),
                ("Calculate 2+2", "tool_task"),
                ("Search in uploaded documents", "clarify"),
                ("Execute the workflow", "clarify"),
                ("Legacy hello", None),
            ):
                settings.v2_enabled = expected_route is not None
                conversation = (
                    await request(
                        owner, "POST", "/api/conversations", {"title": "Synthetic query route"}, 201
                    )
                ).json()
                cid = conversation["id"]
                await request(other, "GET", f"/api/conversations/{cid}/messages", expected=404)
                await request(
                    anonymous, "POST", f"/api/conversations/{cid}/messages", {"content": query}, 401
                )
                response = await request(
                    owner, "POST", f"/api/conversations/{cid}/messages", {"content": query}
                )
                stream = events(response.text)
                assert stream[0][0] == "start" and stream[-1][0] == "done"
                assert all(kind != "error" for kind, _ in stream)
                run_id = UUID(stream[0][1]["run_id"])
                threads.append(run_id)
                routed = [payload for kind, payload in stream if kind == "routing"]
                if expected_route is not None:
                    assert len(routed) == 1 and routed[0]["route"] == expected_route
                    assert routed[0]["provider"] == "rules" and routed[0]["input_tokens"] == 0
                else:
                    assert not routed
                async with SessionFactory() as session:
                    run = await session.get(AgentRun, run_id)
                    assert run.status == "completed" and run.finished_at is not None
                    assert (
                        await session.scalar(
                            select(func.count(Message.id)).where(
                                Message.conversation_id == UUID(cid)
                            )
                        )
                        == 2
                    )
                    count = await session.scalar(
                        select(func.count(ModelCall.id)).where(
                            ModelCall.conversation_id == UUID(cid)
                        )
                    )
                    assert count == len(models[-1].requests)
                assert models[-1].closed
                checkpoint = await saver.aget_tuple(agent_checkpoint_config(run_id))
                if expected_route == "clarify":
                    assert count == 0 and checkpoint is None
                    assert stream[-1][1]["usage"]["total_tokens"] == 0
                else:
                    assert checkpoint is not None
                    saved = checkpoint.checkpoint["channel_values"]["routing_decision"]
                    if expected_route:
                        assert RoutingDecision.model_validate(saved).route == expected_route
                    else:
                        assert saved is None
                    if expected_route == "chat":
                        assert models[-1].requests == [None]
            assert len(calls) == 1
            # Same routing pin survives a real PostgreSQL checkpoint roundtrip/resume.
            context = ToolContext(
                user_id=users[0], conversation_id=uuid4(), run_id=uuid4(), user_role="user"
            )
            threads.append(context.run_id)
            try:
                _ = [
                    item
                    async for item in stream_agent_graph(
                        model=Model(fail=True),
                        messages=[{"role": "user", "content": "Hello"}],
                        registry=registry,
                        context=context,
                        graph_checkpointer=saver,
                        routing_flags=FeatureFlags(enabled=True, routing=True),
                    )
                ]
            except RuntimeError as exc:
                assert str(exc) == "synthetic interrupted route"
            else:
                raise AssertionError("Expected interrupted model")
            resumed = Model()
            items = [
                item
                async for item in stream_agent_graph(
                    model=resumed,
                    messages=[],
                    registry=registry,
                    context=context,
                    graph_checkpointer=saver,
                    resume=True,
                    routing_flags=FeatureFlags(),
                )
            ]
            assert items[0].route == "chat" and resumed.requests == [None]
            return {
                "status": "passed",
                "http_sse_cases": 5,
                "real_cookie_auth": True,
                "owner_isolation": True,
                "rules_before_graph": True,
                "clarification_zero_model_calls": True,
                "chat_zero_tool_schemas": True,
                "tool_task_existing_executor": True,
                "default_off_legacy_path": True,
                "postgres_checkpoint_route_roundtrip": True,
                "pinned_route_resume": True,
                "llm_provider_calls": 0,
                "jev_calls": 0,
            }
    finally:
        settings.v2_enabled = old_master
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        for run_id in threads:
            await saver.adelete_thread(agent_thread_id(run_id))
        async with SessionFactory() as session:
            await session.execute(delete(User).where(User.id.in_(users)))
            await session.commit()
        await close_langgraph_checkpoints()
        await close_langsmith_tracing()
        await engine.dispose()


def main():
    print(json.dumps(asyncio.run(check()), indent=2))


if __name__ == "__main__":
    main()
