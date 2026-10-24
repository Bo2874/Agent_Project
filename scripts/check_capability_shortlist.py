"""HTTP/SSE + PostgreSQL probe for injected capability choice, no Jev provider calls."""

import asyncio
import json
from copy import deepcopy
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete

from agent_core.contracts import CapabilityDescriptor
from agent_core.graph.checkpointing import agent_checkpoint_config, agent_thread_id
from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.routing.shortlist import CapabilityChoice
from agent_core.skills import SkillRegistry
from agent_core.skills.spec import SkillMetadata, SkillSpec
from agent_core.tools import ToolRegistry, ToolResult, ToolRoutingPolicy, create_context_tool
from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.dependencies_decisions import get_decision_provider
from backend.dependencies_llm import get_chat_model
from backend.dependencies_skills import get_skill_registry
from backend.dependencies_tools import get_tool_registry
from backend.main import app
from backend.models import AgentRun, User
from backend.services.langgraph_checkpoints import (
    close_langgraph_checkpoints,
    get_langgraph_checkpointer,
    start_langgraph_checkpoints,
)
from backend.services.langsmith_tracing import close_langsmith_tracing, start_langsmith_tracing
from scripts.auth_probe_diagnostics import auth_diagnostics
from scripts.check_query_routing import events
from scripts.shortlist_policy_fixture import ShortlistPolicyFixture


class Model:
    provider, model = "fixture", "shortlist-postgres-offline"

    def __init__(self, *tools_to_call):
        self.calls = list(tools_to_call)
        self.requests = []

    async def stream(self, messages, tools=None, user_id=None):
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        if self.calls:
            name = self.calls.pop(0)
            yield LLMChunk(
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0, id=f"call_{len(self.requests)}", name=name, arguments="{}"
                    ),
                ),
                finish_reason="tool_calls",
                usage=TokenUsage(1, 1, 2),
            )
        else:
            yield LLMChunk(
                content="Synthetic selected-capability answer",
                finish_reason="stop",
                usage=TokenUsage(1, 1, 2),
            )

    async def aclose(self):
        pass


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
        raise RuntimeError("Use a disposable Compose DB, routing on, plugins/memory/tracing off")
    old_overrides = dict(app.dependency_overrides)
    suffix = uuid4().hex
    users, threads, calls, loads = [], [], [], []
    registry, skills = ToolRegistry(), SkillRegistry()
    for name in ("calculator", "read_data"):

        async def handle(context, arguments, binding=name):
            calls.append(binding)
            return ToolResult.success("private observation body", data={"secret": "private data"})

        registry.register(
            create_context_tool(
                name=name,
                description=f"Calculate using {name}",
                args_schema={"type": "object", "properties": {}},
                coroutine=handle,
            ),
            routing=ToolRoutingPolicy(keywords=frozenset({"calculate"})),
        )
    metadata = SkillMetadata(
        name="calculation_report",
        version="1.0.0",
        description="Calculate report",
        required_tools=("calculator",),
    )

    def load_skill():
        loads.append(True)
        return SkillSpec(metadata, "Trusted synthetic report instructions")

    async def validate_skill():
        pass

    skills.register_loader(
        metadata,
        load_skill,
        validator=validate_skill,
        metadata_validator=validate_skill,
        capability=CapabilityDescriptor(
            capability_id="fixture.skill.report",
            kind="skill",
            name="calculation_report",
            description="Calculate report",
            version="1.0.0",
        ),
    )
    start_langsmith_tracing()
    await start_langgraph_checkpoints()
    saver = get_langgraph_checkpointer()
    try:
        app.dependency_overrides[get_tool_registry] = lambda: registry
        app.dependency_overrides[get_skill_registry] = lambda: skills
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://probe"
        ) as client:
            response = await client.post(
                "/api/auth/register",
                json={
                    "email": f"shortlist-{suffix}@example.com",
                    "password": f"Fixture@{suffix}",
                    "full_name": "Synthetic capability choice",
                },
            )
            assert response.status_code == 201
            users.append(UUID(response.json()["user"]["id"]))
            scenarios = (
                (
                    ("Tool: calculator", "Tool: read_data", "none"),
                    ("calculator", "read_data"),
                    [["calculator"], ["read_data"], []],
                    ["calculator", "read_data"],
                ),
                (
                    ("Skill: calculation_report", "none"),
                    ("calculator",),
                    [["calculator"], []],
                    ["calculator"],
                ),
                (("none", "none"), ("calculator",), [[], []], []),
            )

            def dependencies(current_model, current_provider):
                async def model_dependency():
                    yield current_model

                def provider_dependency():
                    return current_provider

                return model_dependency, provider_dependency

            for answers, tool_calls, expected_schemas, expected_calls in scenarios:
                provider, model = ShortlistPolicyFixture(*answers), Model(*tool_calls)
                model_dep, provider_dep = dependencies(model, provider)
                app.dependency_overrides[get_chat_model] = model_dep
                app.dependency_overrides[get_decision_provider] = provider_dep
                conversation = await client.post(
                    "/api/conversations", json={"title": "Shortlist fixture"}
                )
                assert conversation.status_code == 201
                cid = conversation.json()["id"]
                calls.clear()
                response = await client.post(
                    f"/api/conversations/{cid}/messages", json={"content": "Calculate report"}
                )
                diagnostics = (
                    await auth_diagnostics(client, response, settings)
                    if response.status_code == 401
                    else {}
                )
                assert response.status_code == 200, diagnostics
                stream = events(response.text)
                assert stream[-1][0] == "done" and all(kind != "error" for kind, _ in stream), (
                    stream
                )
                run_id = UUID(stream[0][1]["run_id"])
                threads.append(run_id)
                assert calls == expected_calls
                schemas = [[d["function"]["name"] for d in (t or ())] for _, t in model.requests]
                assert schemas == expected_schemas
                assert len(provider.requests) == len(answers)
                assert "private observation" not in " ".join(r.state for r in provider.requests)
                checkpoint = await saver.aget_tuple(agent_checkpoint_config(run_id))
                saved = checkpoint.checkpoint["channel_values"]
                choice = CapabilityChoice.model_validate(saved["capability_choice"])
                assert choice.outcome == "none" and choice.step_number == len(model.requests)
                assert saved["loaded_tool_names"] == [] and saved["selected_skill"] is None
                async with SessionFactory() as session:
                    run = await session.get(AgentRun, run_id)
                    assert run.status == "completed"
            assert len(loads) == 1
            return {
                "status": "passed",
                "http_sse_cases": 3,
                "real_cookie_auth": True,
                "decision_dependency_injection": True,
                "tool_switch_after_observation": True,
                "skill_instructions_lazy": True,
                "none_blocks_unsolicited_tool_call": True,
                "postgres_capability_choice_roundtrip": True,
                "observation_status_only": True,
                "fixture_boundary": "project DecisionProvider only; not a Jev SDK mock",
                "jev_calls": 0,
                "llm_provider_calls": 0,
            }
    finally:
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
