import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from mcp.types import TextContent, Tool

from agent_core.mcp import MCPClientAdapter, MCPConnectionError, MCPStdioConfig
from agent_core.plugins.manager import PluginManager
from agent_core.tools import ToolRegistry, ToolResult, create_context_tool
from agent_core.tools.registry import DuplicateToolError
from backend.core.config import Settings
from backend.core.mcp_config import load_stdio_configs
from backend.database import get_db_session
from backend.dependencies import get_optional_user
from backend.models import User

ROOT = Path(__file__).resolve().parents[1]


def config(name="good", **kwargs):
    return MCPStdioConfig(name=name, command="trusted-executable", **kwargs)


@pytest.fixture
def host_files(tmp_path, monkeypatch):
    from backend.core import mcp_config

    monkeypatch.setattr(mcp_config, "PROJECT_ROOT", tmp_path)
    directory = tmp_path / "plugins/manifests"
    directory.mkdir(parents=True)
    manifest = json.loads((ROOT / "plugins/manifests/demo.json").read_text())
    profile = json.loads((ROOT / "plugins/stdio/servers.json").read_text(encoding="utf-8"))
    path = tmp_path / "stdio.json"

    def load(change=None, **settings):
        if change:
            change(profile, manifest)
        (directory / "demo.json").write_text(json.dumps(manifest))
        path.write_text(json.dumps(profile))
        return load_stdio_configs(
            Settings(
                _env_file=None,
                db_password="offline-only",
                auth_secret_key="x" * 32,
                mcp_stdio_config_path=path,
                **settings,
            )
        )

    return load


def test_bundled_profile_preserves_legacy_names_and_kill_switch(host_files):
    (entry,) = host_files()
    assert entry.name == "demo" and entry.bindings is None
    assert entry.expected_release == "0.1.0"
    assert "get_admin_runtime_info" in entry.admin_only_tools
    assert host_files(mcp_demo_enabled=False) == ()


def test_canonical_profile_is_feature_gated_and_uses_host_policy(host_files):
    def canonical(profile, manifest):
        profile["servers"][0]["legacy_aliases"] = False

    assert host_files(canonical, v2_enabled=False) == ()
    (entry,) = host_files(v2_enabled=True, v2_plugins_enabled=True)
    assert entry.name == "demo__main"
    binding = next(binding for binding in entry.bindings if binding.remote_name == "calculate")
    assert binding.local_name == "demo__main__calculate"
    assert binding.policy.idempotent is False and binding.policy.max_retries == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda p, m: p.update(api_key="private-canary"),
        lambda p, m: p["profiles"][0].update(env={"TOKEN": "private-canary"}),
        lambda p, m: p["profiles"][0].update(command="python"),
        lambda p, m: p["profiles"][0].update(release="9.0.0"),
        lambda p, m: p["profiles"].append(p["profiles"][0]),
        lambda p, m: p["servers"].append(p["servers"][0]),
        lambda p, m: p["servers"][0].update(manifest="../../.env"),
        lambda p, m: p["servers"][0].update(server_id="missing"),
        lambda p, m: p["servers"][0].update(routing_keywords_by_tool={"unknown": ["x"]}),
        lambda p, m: m["servers"][0].update(transport="streamable_http"),
        lambda p, m: m["servers"][0].update(credentials=[{"slot": "token", "kind": "api_key"}]),
        lambda p, m: m.update(id="another.plugin"),
    ],
)
def test_invalid_profiles_fail_before_start_without_echoing_private_input(host_files, change):
    with pytest.raises(ValueError) as failure:
        host_files(change)
    assert "private-canary" not in str(failure.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("startup_timeout_seconds", 0),
        ("health_timeout_seconds", float("nan")),
        ("request_timeout_seconds", float("inf")),
        ("failure_threshold", 0),
        ("max_discovery_pages", 101),
        ("max_discovered_tools", 0),
        ("max_plugin_concurrency", 17),
    ],
)
def test_stdio_limits_reject_invalid_values(field, value):
    with pytest.raises(ValueError):
        config(**{field: value})


def test_registry_batch_failure_keeps_all_previous_tools():
    async def handler(_, arguments):
        return ToolResult.success("ok")

    a = create_context_tool(
        name="a", description="a", args_schema={"type": "object"}, coroutine=handler
    )
    b = create_context_tool(
        name="b", description="b", args_schema={"type": "object"}, coroutine=handler
    )
    registry, staged = ToolRegistry(), ToolRegistry()
    registry.register(a)
    staged.register(b)
    staged.register(a)
    with pytest.raises(DuplicateToolError):
        registry.register_batch((staged.get("b"), staged.get("a")))
    assert [tool.name for tool in registry.list_tools()] == ["a"]
    old = registry.get("a")
    registry.unregister("a")
    registry.register(a)
    registry.unregister_owned((old,))
    assert "a" in registry


def test_adapter_invalid_discovery_does_not_publish_partial_batch():
    adapter, registry = MCPClientAdapter(config()), ToolRegistry()
    with pytest.raises(ValueError):
        adapter._register_tools(
            registry,
            [
                Tool(name="okay", input_schema={"type": "object"}),
                Tool(name="bad.name", input_schema={"type": "object"}),
            ],
        )
    assert len(registry) == 0


def test_missing_declared_tool_cannot_publish_any_registration():
    adapter = MCPClientAdapter(config(expected_remote_names=frozenset({"required"})))
    registry = ToolRegistry()
    with pytest.raises(MCPConnectionError, match="missing"):
        adapter._register_tools(registry, [Tool(name="extra", input_schema={"type": "object"})])
    assert len(registry) == 0


@pytest.mark.asyncio
async def test_shared_plugin_semaphore_bounds_calls_across_servers():
    semaphore = asyncio.Semaphore(1)
    started, release = asyncio.Event(), asyncio.Event()
    order = []

    async def first_call(*args, **kwargs):
        order.append("first")
        started.set()
        await release.wait()
        return SimpleNamespace(
            content=[TextContent(type="text", text="ok")], structured_content={}, is_error=False
        )

    async def second_call(*args, **kwargs):
        order.append("second")
        return await first_result()

    async def first_result():
        return SimpleNamespace(
            content=[TextContent(type="text", text="ok")], structured_content={}, is_error=False
        )

    first, second = MCPClientAdapter(config("first")), MCPClientAdapter(config("second"))
    first._client = SimpleNamespace(call_tool=first_call)
    second._client = SimpleNamespace(call_tool=second_call)
    first.plugin_semaphore = second.plugin_semaphore = semaphore
    one = asyncio.create_task(first._make_handler("echo")(None, {}))
    await started.wait()
    two = asyncio.create_task(second._make_handler("echo")(None, {}))
    try:
        await asyncio.sleep(0)
        assert order == ["first"]
    finally:
        release.set()
        results = await asyncio.gather(one, two)
    assert order == ["first", "second"] and all(not result.is_error for result in results)


@pytest.mark.asyncio
async def test_queue_timeout_does_not_count_as_server_failure():
    adapter = MCPClientAdapter(config(request_timeout_seconds=0.02))
    adapter._client = SimpleNamespace()
    adapter.plugin_semaphore = asyncio.Semaphore(0)
    failures = []
    adapter.on_failure = failures.append
    result = await adapter._make_handler("echo")(None, {})
    assert result.error_code == "tool_timeout" and not failures


@pytest.mark.asyncio
async def test_healthcheck_does_not_fail_busy_server():
    adapter = MCPClientAdapter(config())
    adapter._client = SimpleNamespace()
    async with adapter._call_lock:
        assert await adapter.healthcheck() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cursor", "pages", "tools", "duplicates"])
async def test_discovery_is_bounded_and_rejects_duplicate_names(mode):
    adapter = MCPClientAdapter(
        config(
            max_discovery_pages=2,
            max_discovered_tools=2 if mode == "duplicates" else 1,
        )
    )

    async def page(**kwargs):
        if mode == "duplicates":
            return SimpleNamespace(tools=[Tool(name="a", input_schema={})] * 2, next_cursor=None)
        if mode == "tools":
            return SimpleNamespace(tools=[Tool(name="a", input_schema={})] * 2, next_cursor="next")
        return SimpleNamespace(tools=[], next_cursor="same" if mode == "cursor" else str(uuid4()))

    with pytest.raises(MCPConnectionError):
        await adapter._list_all_tools(SimpleNamespace(list_tools=page))


@pytest.fixture
def fake_adapters(monkeypatch):
    from agent_core.plugins import manager

    instances = []
    modes = {}

    class Adapter:
        protocol_version = "fixture-protocol"

        def __init__(self, config):
            self.config = config
            self.on_failure = None
            self.registry = None
            self.owned = ()
            self.owner = None
            self.closed = False
            instances.append(self)

        async def connect_and_register(self, registry):
            self.owner = asyncio.current_task()
            if modes.get(self.config.name) == "hang":
                await asyncio.Event().wait()
            if modes.get(self.config.name) == "fail":
                raise RuntimeError("private-transport-detail")

            async def handler(_, arguments):
                return ToolResult.success("ok")

            tool = create_context_tool(
                name=f"{self.config.name}__test",
                description="fixture",
                args_schema={"type": "object"},
                coroutine=handler,
            )
            registry.register(tool)
            self.registry, self.owned = registry, (registry.get(tool.name),)
            return (tool,)

        async def healthcheck(self):
            assert asyncio.current_task() is self.owner
            return modes.get(self.config.name) != "unhealthy"

        def detach(self):
            if self.registry:
                self.registry.unregister_owned(self.owned)

        async def close(self):
            assert asyncio.current_task() is self.owner
            self.detach()
            self.closed = True
            if modes.get(self.config.name) == "close-fail":
                raise RuntimeError("private-shutdown-detail")

    monkeypatch.setattr(manager, "MCPClientAdapter", Adapter)
    return instances, modes


@pytest.mark.asyncio
async def test_independent_startup_timeout_idempotence_and_owner_task_cleanup(fake_adapters):
    instances, modes = fake_adapters
    modes.update(bad="fail", hanging="hang")
    registry = ToolRegistry()
    manager = PluginManager(
        registry,
        (
            config("good"),
            config("bad"),
            config("hanging", startup_timeout_seconds=0.02),
        ),
    )
    try:
        states = {row.name: row for row in await manager.start()}
        assert states["good"].status == "healthy"
        assert states["hanging"].error_code == "startup_timeout"
        assert states["bad"].error_code == "startup_failed"
        assert len(registry) == 1
        await manager.start()
        assert len(instances) == 3
        assert "private-transport-detail" not in json.dumps(manager.health_payload())
    finally:
        await manager.close()
    assert len(registry) == 0 and all(instance.closed for instance in instances)


@pytest.mark.asyncio
async def test_health_opens_circuit_removes_only_failed_server_and_restart_has_cooldown(
    fake_adapters,
):
    _, modes = fake_adapters
    manager = PluginManager(
        ToolRegistry(),
        (
            config("good"),
            config(
                "flaky",
                health_interval_seconds=0.01,
                failure_threshold=2,
                restart_cooldown_seconds=0.03,
            ),
        ),
    )
    try:
        await manager.start()
        modes["flaky"] = "unhealthy"
        async with asyncio.timeout(1):
            while not manager.health_payload()["degraded"] or "flaky__test" in manager.registry:
                await asyncio.sleep(0.005)
        assert "good__test" in manager.registry
        with pytest.raises(RuntimeError, match="cooldown"):
            await manager.restart("flaky")
        modes.pop("flaky")
        await asyncio.sleep(0.08)
        assert (await manager.restart("flaky")).status == "healthy"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_shutdown_failure_does_not_skip_other_servers(fake_adapters):
    instances, modes = fake_adapters
    manager = PluginManager(ToolRegistry(), (config("first"), config("second")))
    await manager.start()
    modes["first"] = "close-fail"
    with pytest.raises(RuntimeError, match="shut down"):
        await manager.close()
    assert all(instance.closed for instance in instances)
    assert len(manager.registry) == 0


@pytest.mark.asyncio
async def test_start_cancellation_cleans_all_owners(fake_adapters):
    instances, modes = fake_adapters
    modes["hanging"] = "hang"
    manager = PluginManager(ToolRegistry(), (config("hanging"), config("good")))
    task = asyncio.create_task(manager.start())
    while not instances:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(instance.closed for instance in instances)
    assert len(manager.registry) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("role,status", [(None, 401), ("user", 403), ("admin", 200)])
async def test_runtime_health_is_admin_only(monkeypatch, role, status):
    from backend.routes import admin

    monkeypatch.setattr(admin, "get_plugin_manager", lambda: PluginManager(ToolRegistry()))
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_db_session] = lambda: None
    app.dependency_overrides[get_optional_user] = lambda: (
        User(id=uuid4(), role=role, is_active=True) if role else None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/api/admin/mcp-runtime")
        assert response.status_code == status
        if status == 200:
            assert response.json() == {"degraded": False, "servers": []}


@pytest.mark.asyncio
async def test_real_stdio_process_isolation_and_cleanup():
    from scripts.check_plugin_manager import check

    result = await check()
    assert result["process_exit_isolated"] and result["child_processes_reaped"]
