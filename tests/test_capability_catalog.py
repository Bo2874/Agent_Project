from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from agent_core.capability_catalog import CapabilityCatalog, CapabilityRecord
from agent_core.context_budget import ContextBudgetError, estimate_tokens
from agent_core.contracts import CapabilityDescriptor
from agent_core.mcp import MCPClientAdapter, MCPStdioConfig
from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    ToolSelectionConfig,
    ToolSelector,
    create_context_tool,
)
from agent_core.tools.registry import ToolRegistryError


async def handler(_, arguments):
    return ToolResult.success("ok")


def register(
    registry,
    name="search",
    *,
    description="Search documents",
    size=0,
    roles=frozenset({"user", "admin"}),
    capability=None,
    **routing,
):
    tool = create_context_tool(
        name=name,
        description=description,
        args_schema={
            "type": "object",
            "properties": {"q": {"type": "string", "description": "x" * size}},
        },
        coroutine=handler,
    )
    registry.register(
        tool,
        policy=ToolExecutionPolicy(allowed_roles=roles),
        routing=ToolRoutingPolicy(**routing),
        capability=capability,
    )
    return registry.get(name)


def descriptor(name="search", **overrides):
    return CapabilityDescriptor(
        capability_id=f"docs.tool.main.{name}",
        kind="tool",
        name=name,
        description="Tìm kiếm tài liệu",
        version="1.0.0",
        plugin_id="test.docs",
        tags=("retrieval",),
        **overrides,
    )


def test_metadata_index_filters_kinds_plugins_tags_and_vietnamese():
    catalog = CapabilityCatalog(
        (
            CapabilityRecord(descriptor(), "docs__main__search"),
            CapabilityRecord(
                CapabilityDescriptor(
                    capability_id="workflow.report",
                    kind="workflow",
                    name="report",
                    description="Make report",
                    version="1",
                ),
                "report",
            ),
        )
    )
    assert len(catalog.search()) == 2
    assert (
        catalog.search("tim kiem", kind="tool", plugin_id="test.docs", tags=("retrieval",))[
            0
        ].binding_name
        == "docs__main__search"
    )
    assert catalog.search("retrieval")[0].descriptor.name == "search"
    assert catalog.search(kind="skill") == ()
    assert catalog.search(tags=("unknown",)) == ()


@pytest.mark.parametrize(
    "limit,query",
    [(0, ""), (4097, ""), (1, "x" * 32769)],
    ids=["zero-limit", "over-limit", "long-query"],
)
def test_search_bounds(limit, query):
    with pytest.raises(ValueError):
        CapabilityCatalog(()).search(query, limit=limit)


def test_snapshot_is_cached_scoped_and_survives_registration_changes():
    registry = ToolRegistry(allowlist={"search", "admin"})
    register(registry, capability=descriptor())
    register(registry, "admin", roles=frozenset({"admin"}))
    register(registry, "hidden")
    old = registry.capability_catalog("user")
    assert old is registry.capability_catalog("user")
    assert [r.binding_name for r in old.records] == ["search"]
    assert len(registry.capability_catalog("admin").records) == 2
    assert registry.capability_catalog("bogus").records == ()
    registry.unregister("search")
    assert registry.capability_catalog("user").version != old.version
    assert len(old.records) == 1
    assert registry.capability_catalog("user").records == ()


def test_duplicate_capability_cannot_publish_or_replace_other_owner():
    registry = ToolRegistry()
    old = register(registry, capability=descriptor())
    version = registry.capability_catalog("user").version
    with pytest.raises(ToolRegistryError):
        register(registry, "collision", capability=descriptor())
    other = register(ToolRegistry(), "other", capability=descriptor())
    with pytest.raises(ToolRegistryError):
        registry.register_batch((other,))
    assert registry.capability_catalog("user").version == version
    registry.unregister("search")
    register(registry)
    with pytest.raises(ToolRegistryError, match="stale"):
        registry.replace_owned((old,), (other,))


def test_lazy_schema_conversion_cache_and_no_shared_mutable_payload(monkeypatch):
    from agent_core.tools import registry as module

    original = module.convert_to_openai_tool
    converted = []

    def convert(tool):
        converted.append(tool["name"])
        return original(tool)

    monkeypatch.setattr(module, "convert_to_openai_tool", convert)
    registry = ToolRegistry()
    for i in range(200):
        register(registry, f"tool_{i}", description=f"Unique topic{i}", size=600)
    assert registry.capability_catalog("user").search("topic42")
    assert converted == []
    selector = ToolSelector(ToolSelectionConfig(lazy_load_threshold=2, max_loaded_tools=1))
    first = selector.select(registry, "user", "topic42")
    assert converted == ["tool_42"]
    first.definitions[0]["function"]["description"] = "tampered"
    second = selector.select(registry, "user", "topic42")
    assert converted == ["tool_42"]
    assert second.definitions[0]["function"]["description"] == "Unique topic42"
    registry.unregister("tool_42")
    register(registry, "tool_42", description="New topic42")
    third = selector.select(registry, "user", "topic42")
    assert third.catalog_version != second.catalog_version
    assert converted == ["tool_42", "tool_42"]


@pytest.mark.parametrize("budget", [0, 60, 150, 600])
def test_budget_prunes_optional_schema_without_truncation(budget):
    registry = ToolRegistry()
    register(registry, "huge", size=9000)
    register(registry, "small")
    result = ToolSelector(ToolSelectionConfig(schema_token_budget=budget)).select(
        registry, "user", "search"
    )
    assert result.schema_tokens <= budget
    assert result.schema_tokens == (
        estimate_tokens(result.definitions) if result.definitions else 0
    )
    assert "huge" in result.omitted_names
    if budget >= 150:
        assert result.loaded_names == {"small"}
        assert (
            result.definitions[0]["function"]["parameters"]["properties"]["q"]["type"] == "string"
        )


def test_required_skill_group_fails_instead_of_silent_partial_execution():
    registry = ToolRegistry()
    register(registry, "one")
    register(registry, "two", size=3000)
    selector = ToolSelector(ToolSelectionConfig(schema_token_budget=180))
    with pytest.raises(ContextBudgetError, match="Required Skill"):
        selector.fit(registry, "user", (), required=("one", "two"), mode="skill")
    with pytest.raises(ContextBudgetError, match="unavailable"):
        selector.fit(registry, "user", (), required=("missing",), mode="skill")


def test_discovery_atomic_replace_and_noop_keep_snapshot_and_stale_handler_closed():
    from mcp.types import Tool

    adapter, registry = (
        MCPClientAdapter(MCPStdioConfig(name="test", command="trusted")),
        ToolRegistry(),
    )
    remote = Tool(name="echo", input_schema={"type": "object"})
    adapter._register_tools(registry, [remote])
    old, version = registry.get("test__echo"), registry.capability_catalog("user").version
    adapter._register_tools(registry, [remote], replacing=True)
    assert registry.get("test__echo") is old
    assert registry.capability_catalog("user").version == version
    with pytest.raises(ValueError):
        adapter._register_tools(
            registry, [Tool(name="echo", input_schema={"type": "array"})], replacing=True
        )
    assert registry.get("test__echo") is old
    adapter._register_tools(
        registry,
        [Tool(name="echo", description="new", input_schema={"type": "object"})],
        replacing=True,
    )
    assert registry.get("test__echo") is not old
    assert registry.capability_catalog("user").version != version


async def test_old_handler_is_rejected_after_discovery_generation_changes():
    from mcp.types import Tool

    adapter, registry = (
        MCPClientAdapter(MCPStdioConfig(name="test", command="trusted")),
        ToolRegistry(),
    )
    remote = Tool(name="echo", input_schema={"type": "object"})
    adapter._client = SimpleNamespace()
    adapter._register_tools(registry, [remote])
    old = registry.get("test__echo").tool
    adapter._register_tools(registry, [], replacing=True)
    result = await old.ainvoke(
        {}, config={"configurable": {"tool_context": ToolContext(uuid4(), uuid4(), "user")}}
    )
    assert result.error_code == "mcp_discovery_changed"


async def test_refresh_bypasses_sdk_cache_and_cannot_publish_after_detach():
    from mcp.types import Tool

    adapter, registry = (
        MCPClientAdapter(MCPStdioConfig(name="test", command="trusted")),
        ToolRegistry(),
    )
    adapter._register_tools(registry, [Tool(name="echo", input_schema={"type": "object"})])
    adapter._registry = registry

    async def list_tools(**kwargs):
        assert kwargs["cache_mode"] == "bypass"
        adapter.detach()
        return SimpleNamespace(tools=[], next_cursor=None)

    adapter._client = SimpleNamespace(list_tools=list_tools)
    with pytest.raises(RuntimeError, match="stale"):
        await adapter.refresh_discovery()
    assert len(registry) == 0


@pytest.mark.parametrize(
    "enabled,role,status",
    [(False, "user", 404), (True, None, 401), (True, "user", 200), (True, "admin", 200)],
)
async def test_metadata_api_filters_before_exposure(monkeypatch, enabled, role, status):
    from backend.core.config import Settings
    from backend.database import get_db_session
    from backend.dependencies import get_optional_user
    from backend.dependencies_tools import get_tool_registry
    from backend.models import User
    from backend.routes import plugins

    registry = ToolRegistry()
    register(registry, capability=descriptor())
    register(registry, "admin", roles=frozenset({"admin"}))
    settings = Settings(
        _env_file=None,
        db_password="offline",
        auth_secret_key="x" * 32,
        v2_enabled=enabled,
        v2_plugins_enabled=enabled,
    )
    monkeypatch.setattr(plugins, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(plugins.router)
    app.dependency_overrides[get_db_session] = lambda: None
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_optional_user] = lambda: (
        User(id=uuid4(), role=role, is_active=True) if role else None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/api/capabilities")
        assert response.status_code == status
        if status == 200:
            payload = response.json()
            assert payload["total"] == (2 if role == "admin" else 1)
            assert "parameters" not in response.text and "input_schema_ref" in response.text
            filtered = await client.get(
                "/api/capabilities?plugin_id=test.docs&tag=retrieval&limit=1"
            )
            assert filtered.json()["total"] == 1


async def test_real_stdio_discovery_between_runs(tmp_path):
    from scripts.check_capability_catalog import check_discovery

    result = await check_discovery(tmp_path)
    assert all(result.values())


def test_lazy_schema_preserves_refs_required_enum_and_numeric_constraints():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "$defs": {"Count": {"type": "integer", "minimum": 1, "maximum": 10}},
        "properties": {
            "count": {"$ref": "#/$defs/Count"},
            "mode": {"type": "string", "enum": ["brief", "full"]},
        },
        "required": ["count", "mode"],
    }
    registry = ToolRegistry()
    registry.register(
        create_context_tool(
            name="constrained",
            description="Constrained query",
            args_schema=schema,
            coroutine=handler,
        )
    )
    selected = ToolSelector().select(registry, "user", "query")
    assert selected.definitions[0]["function"]["parameters"] == schema


class ProbeModel:
    provider, model = "fake", "catalog-probe"

    def __init__(self, mutate=None):
        self.requests = []
        self.mutate = mutate

    async def stream(self, messages, tools=None, user_id=None):
        from agent_core.llm.types import LLMChunk, LLMToolCallDelta

        self.requests.append((deepcopy(messages), deepcopy(tools)))
        if len(self.requests) == 1 and self.mutate:
            self.mutate()
            yield LLMChunk(
                finish_reason="tool_calls",
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0, id="catalog_call", name="search", arguments='{"q":"x"}'
                    ),
                ),
            )
        else:
            yield LLMChunk(content="done", finish_reason="stop")

    async def aclose(self):
        pass


async def test_graph_uses_actual_context_schema_budget_before_model_call():
    from agent_core.context_budget import ContextTokenBudget
    from agent_core.graph import stream_agent_graph

    registry, model = ToolRegistry(), ProbeModel()
    register(registry, "huge", size=12000)
    register(registry, "small")
    events = []

    async def capture(event):
        events.append(event)

    _ = [
        item
        async for item in stream_agent_graph(
            model=model,
            registry=registry,
            messages=[{"role": "user", "content": "search"}],
            context=ToolContext(uuid4(), uuid4(), "user"),
            event_handler=capture,
            context_budget=ContextTokenBudget(tool_tokens=150),
        )
    ]
    assert [d["function"]["name"] for d in model.requests[0][1]] == ["small"]
    assert events[0].context_usage.tool_tokens <= 150
    assert events[0].context_usage.available_tool_count == 2


async def test_graph_cannot_execute_new_registration_under_old_schema():
    import json

    from agent_core.graph import stream_agent_graph

    registry = ToolRegistry()
    register(registry)

    def mutate():
        registry.unregister("search")
        register(registry, description="New generation")

    model = ProbeModel(mutate)
    _ = [
        item
        async for item in stream_agent_graph(
            model=model,
            registry=registry,
            messages=[{"role": "user", "content": "search"}],
            context=ToolContext(uuid4(), uuid4(), "user"),
        )
    ]
    observation = json.loads(model.requests[1][0][-1]["content"])
    assert observation["error_code"] == "tool_catalog_changed"
    assert model.requests[0][1] == model.requests[1][1]


@pytest.mark.parametrize(
    "enabled,role,status",
    [
        (False, "admin", 404),
        (True, None, 401),
        (True, "user", 403),
        (True, "admin", 200),
    ],
)
async def test_discovery_mutation_is_feature_gated_and_admin_only(
    monkeypatch, enabled, role, status
):
    from backend.core.config import Settings
    from backend.database import get_db_session
    from backend.dependencies import get_optional_user
    from backend.models import User
    from backend.routes import admin

    settings = Settings(
        _env_file=None,
        db_password="offline",
        auth_secret_key="x" * 32,
        v2_enabled=enabled,
        v2_plugins_enabled=enabled,
    )
    calls, audits = [], []

    class Manager:
        async def refresh_discovery(self, name):
            calls.append(name)
            return {"server": name, "tool_names": ["echo"], "status": "refreshed"}

    async def audit(*args, **kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(admin, "get_settings", lambda: settings)
    monkeypatch.setattr(admin, "get_plugin_manager", Manager)
    monkeypatch.setattr(admin, "record_audit_event", audit)
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_db_session] = lambda: None
    app.dependency_overrides[get_optional_user] = lambda: (
        User(id=uuid4(), role=role, is_active=True) if role else None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post("/api/admin/mcp-runtime/test/discover")
    assert response.status_code == status
    assert calls == (["test"] if status == 200 else [])
    assert len(audits) == (1 if status == 200 else 0)
