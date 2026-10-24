import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_core.graph import AgentNodeExecutionError, stream_agent_graph
from agent_core.mcp import MCPClientAdapter, MCPStdioConfig
from agent_core.plugins.skills import PluginSkillSource
from agent_core.skills import SkillRegistry
from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    create_context_tool,
)
from agent_core.tools.types import ToolAuthorizationError
from backend.core.plugin_packages import ReviewedPackages
from scripts.plugin_skill_fixture import make_manifest, write_package


@pytest.fixture
def package(tmp_path, monkeypatch):
    from backend.core import plugin_packages

    monkeypatch.setattr(plugin_packages, "PROJECT_ROOT", tmp_path)
    manifest = make_manifest()
    config, directory = write_package(tmp_path, manifest)
    packages = ReviewedPackages(config)
    (source,) = packages.sources(manifest)
    return source, config, directory, manifest


def test_package_metadata_is_lazy_and_namespaced(package, monkeypatch):
    source, _, _, _ = package
    reads = []
    original = PluginSkillSource._read

    def read(self, filename, maximum):
        reads.append(filename)
        return original(self, filename, maximum)

    monkeypatch.setattr(PluginSkillSource, "_read", read)
    source.check_metadata()
    assert "instructions.md" not in reads
    assert source.metadata.name == "fixture__skill__brief"
    assert source.metadata.required_tools == ("fixture__main__echo",)
    assert source.load().metadata == source.metadata
    assert "instructions.md" in reads


@pytest.mark.parametrize("field", ["version", "required_tools", "description", "tags"])
def test_package_dependency_and_metadata_must_match_reviewed_manifest(package, field):
    source, _, directory, _ = package
    path = directory / "skill.json"
    payload = json.loads(path.read_text())
    if field == "required_tools":
        payload[field] = ["other__main__admin"]
    else:
        payload["metadata"][field] = ["wrong"] if field == "tags" else "2.0.0"
    path.write_text(json.dumps(payload))
    with pytest.raises(ToolAuthorizationError):
        source.check_metadata()


def test_cached_instructions_cannot_hide_package_tamper(package):
    source, _, directory, manifest = package
    registry = SkillRegistry()

    async def validate():
        source.check()

    registry.register_loader(
        source.metadata,
        source.load,
        validator=validate,
        capability=next(c for c in manifest.capabilities() if c.kind == "skill"),
    )
    registry.load(source.metadata.name, available_tools=source.metadata.required_tools)
    (directory / "instructions.md").write_text("unreviewed changed instructions")
    with pytest.raises(ToolAuthorizationError, match="Capabilities changed"):
        registry.load(source.metadata.name, available_tools=source.metadata.required_tools)


def test_package_config_and_manifest_versions_invalidate_snapshots(package):
    _, config, _, manifest = package
    catalog = ReviewedPackages(config)
    assert catalog.current()
    config.write_text('{"schema_version":"1.0","packages":[]}')
    assert not catalog.current()
    with pytest.raises(ValueError):
        catalog.sources(manifest.model_copy(update={"version": "2.0.0"}))


@pytest.mark.parametrize("root", ["../outside", "/absolute", "a//b", "a/./b"])
def test_package_root_rejects_traversal(package, root):
    _, config, _, _ = package
    payload = json.loads(config.read_text())
    payload["packages"][0]["relative_root"] = root
    config.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        ReviewedPackages(config)


def test_symlink_escape_is_rejected(package, tmp_path):
    source, _, _, _ = package
    escaped = replace(source, relative_root="../../outside")
    with pytest.raises(ToolAuthorizationError):
        escaped.load()
    # Real filesystem link, not just lexical traversal; privilege availability is explicit.
    target = tmp_path / "outside"
    target.mkdir()
    link = source.anchor / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        # Windows junctions need no Developer Mode and are checked by realpath confinement.
        import os

        if os.name != "nt":
            raise
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )
    with pytest.raises(ToolAuthorizationError):
        replace(source, relative_root="link").load()


async def tool(_, arguments):
    return ToolResult.success("ok")


def registry_for_test(policy=None, coroutine=tool):
    registry = ToolRegistry()
    registry.register(
        create_context_tool(
            name="echo", description="Echo", args_schema={"type": "object"}, coroutine=coroutine
        ),
        policy=policy,
    )
    return registry


async def test_revocation_precedes_executor_memoization():
    revoked = False

    async def guard(name):
        if revoked:
            raise ToolAuthorizationError()

    context = ToolContext(uuid4(), uuid4(), "user", authorization_guard=guard)
    registry = registry_for_test(ToolExecutionPolicy(idempotent=True))
    executor = ToolExecutor()
    assert not (await executor.execute(registry, context, "echo", {})).is_error
    revoked = True
    assert (
        await executor.execute(registry, context, "echo", {})
    ).error_code == "plugin_authorization_changed"


async def test_retry_rechecks_authority_after_backoff():
    calls = []

    async def guard(name):
        if calls:
            raise ToolAuthorizationError()

    async def fail(_, arguments):
        calls.append(1)
        return ToolResult.failure("temporary", error_code="temporary_failure")

    registry = registry_for_test(
        ToolExecutionPolicy(idempotent=True, max_retries=2, retry_backoff_seconds=0), fail
    )
    context = ToolContext(uuid4(), uuid4(), "user", authorization_guard=guard)
    result = await ToolExecutor().execute(registry, context, "echo", {})
    assert result.error_code == "plugin_authorization_changed" and calls == [1]


async def test_dispatch_rechecks_after_waiting_for_plugin_semaphore():
    revoked, sent = False, []

    async def guard(name):
        if revoked:
            raise ToolAuthorizationError()

    async def send(*args, **kwargs):
        sent.append(1)

    adapter = MCPClientAdapter(MCPStdioConfig(name="test", command="trusted"))
    adapter._client = SimpleNamespace(call_tool=send)
    adapter.plugin_semaphore = asyncio.Semaphore(0)
    context = ToolContext(uuid4(), uuid4(), "user", authorization_guard=guard)
    pending = asyncio.create_task(adapter._make_handler("echo")(context, {}))
    await asyncio.sleep(0)
    revoked = True
    adapter.plugin_semaphore.release()
    result = await pending
    assert result.error_code == "plugin_authorization_changed" and sent == []


async def test_skill_policy_forces_approval_for_required_read_tool():
    registry = registry_for_test()
    context = ToolContext(uuid4(), uuid4(), "user")
    executor = ToolExecutor(force_approval=True)
    assert executor.needs_approval(registry, context, "echo", {})
    assert (
        await executor.execute(registry, context, "echo", {})
    ).error_code == "tool_approval_required"
    assert not (
        await executor.execute(registry, context, "echo", {}, approval_granted=True)
    ).is_error


class Model:
    provider, model = "fake", "plugin-skill-probe"

    def __init__(self):
        self.calls = 0

    async def stream(self, messages, tools=None, user_id=None):
        from agent_core.llm.types import LLMChunk

        self.calls += 1
        yield LLMChunk(content="done", finish_reason="stop")

    async def aclose(self):
        pass


async def test_selected_skill_revalidation_stops_model_after_revoke(package):
    source, _, _, manifest = package
    registry, model = ToolRegistry(), Model()
    registry.register(
        create_context_tool(
            name="fixture__main__echo",
            description="Echo",
            args_schema={"type": "object"},
            coroutine=tool,
        )
    )
    skills = SkillRegistry()

    async def denied():
        raise ToolAuthorizationError()

    skills.register_loader(
        source.metadata,
        source.load,
        validator=denied,
        capability=next(c for c in manifest.capabilities() if c.kind == "skill"),
    )
    with pytest.raises(AgentNodeExecutionError) as failure:
        _ = [
            item
            async for item in stream_agent_graph(
                model=model,
                registry=registry,
                messages=[{"role": "user", "content": "fixture skill brief pluginreport"}],
                context=ToolContext(uuid4(), uuid4(), "user"),
                skill_registry=skills,
            )
        ]
    assert failure.value.code == "plugin_authorization_changed" and model.calls == 0


async def test_resume_rejects_authority_version_change():
    from langgraph.checkpoint.memory import InMemorySaver

    context = ToolContext(uuid4(), uuid4(), "user", uuid4(), capability_version="grant-1")
    saver = InMemorySaver()
    _ = [
        item
        async for item in stream_agent_graph(
            model=Model(),
            registry=ToolRegistry(),
            context=context,
            graph_checkpointer=saver,
            messages=[{"role": "user", "content": "hi"}],
        )
    ]
    with pytest.raises(AgentNodeExecutionError) as failure:
        _ = [
            item
            async for item in stream_agent_graph(
                model=Model(),
                registry=ToolRegistry(),
                context=replace(context, capability_version="grant-2"),
                graph_checkpointer=saver,
                messages=[],
                resume=True,
            )
        ]
    assert failure.value.code == "plugin_runtime_changed"


async def test_owned_registry_enforces_owner_and_authority_without_context_callback():
    registry = registry_for_test(ToolExecutionPolicy(idempotent=True))
    owner, other = uuid4(), uuid4()
    registry.owner_user_id = owner
    revoked = False

    async def guard(name):
        if revoked:
            raise ToolAuthorizationError()

    registry.authorization_guard = guard
    executor = ToolExecutor()
    context = ToolContext(owner, uuid4(), "user")
    assert not (await executor.execute(registry, context, "echo", {})).is_error
    assert (
        await executor.execute(registry, replace(context, user_id=other), "echo", {})
    ).error_code == "tool_permission_denied"
    revoked = True
    assert (
        await executor.execute(registry, context, "echo", {})
    ).error_code == "plugin_authorization_changed"


async def test_feature_off_retains_v1_objects_and_does_no_db_work(monkeypatch):
    from backend.core.config import Settings
    from backend.services import plugin_runtime as module

    settings = Settings(_env_file=None, db_password="offline", auth_secret_key="x" * 32)
    monkeypatch.setattr(module, "get_settings", lambda: settings)

    def forbidden():
        raise AssertionError("V1 must not open a plugin DB session")

    monkeypatch.setattr(module, "SessionFactory", forbidden)
    registry, skills, context = (
        ToolRegistry(),
        SkillRegistry(),
        ToolContext(uuid4(), uuid4(), "user"),
    )
    async with module.plugin_runtime(registry, skills, context) as runtime:
        assert (
            runtime.registry is registry and runtime.skills is skills and runtime.context is context
        )


def test_run_view_excludes_ungranted_shared_plugin_bindings_and_preserves_allowlist():
    manifest = make_manifest()
    registry = ToolRegistry(allowlist={"echo"})
    entry = registry_for_test().get("echo")
    registry.register(
        entry.tool,
        capability=next(c for c in manifest.capabilities() if c.kind == "tool"),
        manifest_fingerprint=manifest.fingerprint(),
    )
    scoped = registry.fork(include_plugins=False)
    assert len(scoped) == 0 and len(registry) == 1
    assert scoped.is_allowlisted("echo") and not scoped.is_allowlisted("blocked")


async def test_model_retry_checks_revocation_before_second_provider_attempt():
    from agent_core.llm.deepseek import LLMProviderError

    registry, revoked = registry_for_test(), False

    async def guard(name):
        if revoked:
            raise ToolAuthorizationError()

    class FailingModel(Model):
        async def stream(self, *args, **kwargs):
            nonlocal revoked
            self.calls += 1
            revoked = True
            raise LLMProviderError("synthetic provider failure")
            yield

    model = FailingModel()
    with pytest.raises(AgentNodeExecutionError) as failure:
        _ = [
            item
            async for item in stream_agent_graph(
                model=model,
                registry=registry,
                messages=[{"role": "user", "content": "echo"}],
                context=ToolContext(uuid4(), uuid4(), "user", authorization_guard=guard),
            )
        ]
    assert model.calls == 1 and failure.value.code == "plugin_authorization_changed"


async def test_inflight_result_is_withheld_and_not_cached_after_revoke():
    revoked = False

    async def guard(name):
        if revoked:
            raise ToolAuthorizationError()

    async def in_flight(_, arguments):
        nonlocal revoked
        revoked = True
        return ToolResult.success("private-result-canary", data={"private": "canary"})

    registry = registry_for_test(ToolExecutionPolicy(idempotent=True), in_flight)
    executor = ToolExecutor()
    result = await executor.execute(
        registry, ToolContext(uuid4(), uuid4(), "user", authorization_guard=guard), "echo", {}
    )
    assert result.error_code == "plugin_authorization_changed"
    assert "canary" not in result.content and not result.data
    assert not executor._successful_results


async def test_anonymous_http_profile_change_invalidates_before_dispatch(tmp_path, monkeypatch):
    from backend.core.config import Settings
    from backend.core.mcp_http_config import load_http_configuration
    from backend.services import plugin_runtime as module
    from backend.services.plugin_credentials import fingerprint

    path = tmp_path / "http.json"
    payload = {
        "schema_version": "1.0",
        "profiles": [
            {"id": "fixture.http", "release": "0.1.0", "endpoint": "https://public.example/mcp"}
        ],
        "servers": [],
    }
    path.write_text(json.dumps(payload))
    settings = Settings(
        _env_file=None,
        db_password="offline",
        auth_secret_key="x" * 32,
        mcp_http_config_path=path,
        v2_enabled=True,
        v2_plugins_enabled=True,
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    runtime = module.RunPluginRuntime(
        ToolRegistry(), SkillRegistry(), ToolContext(uuid4(), uuid4(), "user")
    )
    runtime.http_profile_pins["echo"] = (
        "fixture.http",
        fingerprint(load_http_configuration(settings).profiles[0]),
    )
    await runtime.authorize_tool("echo")
    payload["profiles"][0]["endpoint"] = "https://other.example/mcp"
    path.write_text(json.dumps(payload))
    with pytest.raises(ToolAuthorizationError) as failure:
        await runtime.authorize_tool("echo")
    assert failure.value.code == "plugin_profile_changed"
