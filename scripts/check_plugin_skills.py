"""Real PostgreSQL/auth/stdio + fake model probe for run-local plugin Skills and revoke."""

import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, update

from agent_core.graph import AgentNodeExecutionError, stream_agent_graph
from agent_core.llm.types import LLMChunk, LLMToolCallDelta
from agent_core.mcp import MCPStdioConfig
from agent_core.mcp.client import MCPToolBinding
from agent_core.plugins.manager import PluginManager
from agent_core.skills import SkillRegistry, SkillToolMiddleware
from agent_core.tools import ToolContext, ToolExecutionPolicy, ToolExecutor, ToolRegistry
from agent_core.tools.types import ToolAuthorizationError
from backend.core import plugin_packages
from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.dependencies_llm import get_chat_model
from backend.dependencies_skills import get_skill_registry
from backend.dependencies_tools import get_tool_registry
from backend.main import app
from backend.models import AuditLog, Plugin, PluginCatalogState, User
from backend.routes import chat
from backend.services.langsmith_tracing import close_langsmith_tracing, start_langsmith_tracing
from backend.services.plugin_runtime import plugin_runtime
from scripts.auth_probe_diagnostics import auth_diagnostics
from scripts.plugin_skill_fixture import make_manifest, write_package


class Model:
    provider, model = "fixture", "plugin-skill-offline"

    def __init__(self, alias, after_call=None):
        self.alias, self.after_call, self.calls = alias, after_call, 0
        self.messages = []

    async def stream(self, messages, tools=None, user_id=None):
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            yield LLMChunk(
                finish_reason="tool_calls",
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0, id="fixture-call", name=self.alias, arguments='{"value":"report"}'
                    ),
                ),
            )
        else:
            yield LLMChunk(content="Synthetic plugin report completed.", finish_reason="stop")

    async def aclose(self):
        pass


async def check():
    settings = get_settings()
    assert settings.db_host == "postgres" and settings.app_env == "development"
    assert settings.v2_features.is_enabled("plugins")
    assert not settings.langsmith_tracing_enabled
    start_langsmith_tracing()
    old_path, old_root = settings.plugin_packages_config_path, plugin_packages.PROJECT_ROOT
    old_overrides = dict(app.dependency_overrides)
    old_checkpointer = chat.get_langgraph_checkpointer
    suffix = uuid4().hex
    plugin_id, namespace = f"skills.{suffix}", f"s{suffix[:8]}"
    manifest = make_manifest(plugin_id, namespace)
    alias, skill_name = f"{namespace}__main__echo", f"{namespace}__skill__brief"
    ids, manager = [], None
    try:
        with tempfile.TemporaryDirectory(prefix="plugin-skill-probe-") as directory:
            root = Path(directory)
            config, skill_directory = write_package(root, manifest)
            plugin_packages.PROJECT_ROOT = root
            settings.plugin_packages_config_path = config
            base = ToolRegistry(allowlist={alias})

            async def start_host(current):
                nonlocal manager
                if manager:
                    await manager.close()
                cap = next(c for c in current.capabilities() if c.kind == "tool")
                manager = PluginManager(
                    base,
                    (
                        MCPStdioConfig(
                            name=f"{namespace}__main",
                            command=sys.executable,
                            args=("-m", "scripts.mcp_stdio_fixture"),
                            expected_release="0.1.0",
                            plugin_id=plugin_id,
                            manifest_fingerprint=current.fingerprint(),
                            bindings=(
                                MCPToolBinding(
                                    "echo", alias, ToolExecutionPolicy(idempotent=True), cap
                                ),
                            ),
                            health_interval_seconds=600,
                        ),
                    ),
                )
                await manager.start()
                assert manager.health()[0].status == "healthy"

            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://probe"
                ) as admin,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://probe"
                ) as owner,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://probe"
                ) as other,
            ):

                async def request(client, method, path, body=None, expected=200):
                    response = await client.request(
                        method, path, **({"json": body} if body else {})
                    )
                    diagnostics = (
                        await auth_diagnostics(client, response, settings)
                        if (response.status_code == 401 and expected != 401)
                        else {}
                    )
                    assert response.status_code == expected, (
                        f"Skill probe {method} {path}: {response.status_code}, "
                        f"expected {expected}; "
                        f"{diagnostics}"
                    )
                    return response.json()

                for role, client in (("admin", admin), ("owner", owner), ("other", other)):
                    user = await request(
                        client,
                        "POST",
                        "/api/auth/register",
                        {
                            "email": f"skill-{role}-{suffix}@example.com",
                            "password": f"Fixture@{suffix}",
                            "full_name": "Synthetic plugin Skill",
                        },
                        201,
                    )
                    ids.append(UUID(user["user"]["id"]))
                async with SessionFactory() as session:
                    await session.execute(
                        update(User).where(User.id == ids[0]).values(role="admin")
                    )
                    await session.commit()

                async def state():
                    return await request(admin, "GET", "/api/admin/plugins")

                async def publish(current):
                    await request(
                        admin,
                        "PUT",
                        f"/api/admin/plugins/{plugin_id}",
                        {
                            "manifest": current.model_dump(mode="json"),
                            "expected_catalog_version": (await state())["catalog_version"],
                        },
                    )
                    await request(
                        admin,
                        "PATCH",
                        f"/api/admin/plugins/{plugin_id}/enabled",
                        {
                            "enabled": True,
                            "expected_catalog_version": (await state())["catalog_version"],
                        },
                    )

                all_caps = [c.capability_id for c in manifest.capabilities()]

                async def grant(user_id, enabled=True, caps=None):
                    await request(
                        admin,
                        "PUT",
                        f"/api/admin/plugins/{plugin_id}/grants/{user_id}",
                        {
                            "enabled": enabled,
                            "capability_ids": all_caps if caps is None else caps,
                            "expected_auth_version": (await state())["auth_version"],
                        },
                    )

                async def enable(client, connection_id, enabled=True):
                    return await request(
                        client,
                        "PATCH",
                        f"/api/plugin-connections/{connection_id}/enabled",
                        {
                            "enabled": enabled,
                            "expected_auth_version": (await state())["auth_version"],
                        },
                    )

                await publish(manifest)
                await start_host(manifest)
                # Dependency validation is enforced by the actual authenticated API.
                await request(
                    admin,
                    "PUT",
                    f"/api/admin/plugins/{plugin_id}/grants/{ids[1]}",
                    {
                        "enabled": True,
                        "capability_ids": [f"{namespace}.skill.brief"],
                        "expected_auth_version": (await state())["auth_version"],
                    },
                    422,
                )
                await grant(ids[1])
                await grant(ids[2], caps=[f"{namespace}.tool.main.echo"])
                connection_ids = []
                for client in (owner, other):
                    connection = await request(
                        client,
                        "POST",
                        "/api/plugin-connections",
                        {
                            "plugin_id": plugin_id,
                            "expected_auth_version": (await state())["auth_version"],
                        },
                        201,
                    )
                    connection_id = connection["connections"][0]["id"]
                    connection_ids.append(connection_id)
                    await enable(client, connection_id)

                context = ToolContext(ids[1], uuid4(), "user", uuid4())
                async with plugin_runtime(base, SkillRegistry(), context) as runtime:
                    assert skill_name in runtime.skills
                    resolution = SkillToolMiddleware(
                        tool_registry=runtime.registry,
                        user_role="user",
                        skill_registry=runtime.skills,
                    ).resolve(skill_name)
                    assert resolution.selected_skill.name == skill_name
                    executor = ToolExecutor()
                    result = await executor.execute(
                        runtime.registry, runtime.context, alias, {"value": "one"}
                    )
                    assert not result.is_error
                    crossed = await executor.execute(
                        runtime.registry,
                        ToolContext(ids[2], context.conversation_id, "user"),
                        alias,
                        {"value": "one"},
                    )
                    assert crossed.error_code == "tool_permission_denied"
                    saver = InMemorySaver()
                    _ = [
                        item
                        async for item in stream_agent_graph(
                            model=Model(alias),
                            registry=runtime.registry,
                            skill_registry=runtime.skills,
                            context=runtime.context,
                            graph_checkpointer=saver,
                            messages=[{"role": "user", "content": skill_name}],
                        )
                    ]
                    await grant(ids[1], False)
                    denied = await executor.execute(
                        runtime.registry, runtime.context, alias, {"value": "one"}
                    )
                    assert denied.error_code == "plugin_authorization_changed"
                    try:
                        await runtime.skills.validate(skill_name)
                    except ToolAuthorizationError:
                        pass
                    else:
                        raise AssertionError("Revoked Skill remained authorized")
                    await grant(ids[1])
                    await enable(owner, connection_ids[0])
                    old = await executor.execute(
                        runtime.registry, runtime.context, alias, {"value": "one"}
                    )
                    assert old.is_error  # Regrant cannot revive an old run.
                async with plugin_runtime(
                    base, SkillRegistry(), replace(context, run_id=uuid4())
                ) as renewed:
                    assert skill_name in renewed.skills
                    assert renewed.context.capability_version != runtime.context.capability_version
                    try:
                        _ = [
                            item
                            async for item in stream_agent_graph(
                                model=Model(alias),
                                registry=renewed.registry,
                                skill_registry=renewed.skills,
                                context=replace(renewed.context, run_id=context.run_id),
                                graph_checkpointer=saver,
                                messages=[],
                                resume=True,
                            )
                        ]
                    except AgentNodeExecutionError as exc:
                        assert exc.code == "plugin_runtime_changed"
                    else:
                        raise AssertionError("Old checkpoint resumed after regrant")
                    # Active graph loses consent after its first model response, before RPC.
                    events = []

                    async def capture(event):
                        events.append(event)
                        if event.kind == "model_completed":
                            await grant(ids[1], False)

                    model = Model(alias)
                    try:
                        _ = [
                            item
                            async for item in stream_agent_graph(
                                model=model,
                                registry=renewed.registry,
                                skill_registry=renewed.skills,
                                context=renewed.context,
                                event_handler=capture,
                                messages=[{"role": "user", "content": skill_name}],
                            )
                        ]
                    except AgentNodeExecutionError as exc:
                        assert exc.code == "plugin_authorization_changed"
                    assert model.calls == 1
                    assert any(
                        e.kind == "tool_completed" and e.tool_result.is_error for e in events
                    )
                await grant(ids[1])
                await enable(owner, connection_ids[0])
                async with plugin_runtime(
                    base, SkillRegistry(), ToolContext(ids[2], uuid4(), "user")
                ) as isolated:
                    assert skill_name not in isolated.skills
                # Real chat/SSE route uses the bridge, not a separate demo-only graph path.
                app.dependency_overrides[get_tool_registry] = lambda: base
                app.dependency_overrides[get_skill_registry] = SkillRegistry
                model = Model(alias)
                app.dependency_overrides[get_chat_model] = lambda: model
                chat.get_langgraph_checkpointer = lambda: InMemorySaver()
                conversation = await request(
                    owner, "POST", "/api/conversations", {"title": "Skill probe"}, 201
                )
                response = await owner.post(
                    f"/api/conversations/{conversation['id']}/messages",
                    json={"content": skill_name},
                )
                assert response.status_code == 200 and "event: done" in response.text, (
                    response.text[-2000:]
                )
                assert skill_name in response.text and model.calls == 2
                assert any(
                    "SKILL_INSTRUCTIONS" in str(m) and skill_name in str(m)
                    for m in model.messages[0]
                )
                # A reviewed release invalidates the loaded run and works in a fresh run.
                async with plugin_runtime(
                    base, SkillRegistry(), replace(context, run_id=uuid4())
                ) as old_runtime:
                    old_runtime.skills.load(skill_name, available_tools={alias})
                    (skill_directory / "instructions.md").write_text("Unreviewed changes")
                    try:
                        await old_runtime.skills.validate(skill_name)
                    except ToolAuthorizationError as exc:
                        assert exc.code == "plugin_package_changed"
                    else:
                        raise AssertionError("Cached instructions bypassed content pin")
                    manifest = make_manifest(plugin_id, namespace, "1.1.0")
                    config, skill_directory = write_package(
                        root, manifest, text="Reviewed release two instructions."
                    )
                    await publish(manifest)
                    await start_host(manifest)
                    await grant(ids[1])
                    await enable(owner, connection_ids[0])
                    try:
                        await old_runtime.skills.validate(skill_name)
                    except ToolAuthorizationError:
                        pass
                    else:
                        raise AssertionError("Old release survived refresh")
                async with plugin_runtime(
                    base, SkillRegistry(), replace(context, run_id=uuid4())
                ) as new_runtime:
                    spec = new_runtime.skills.load(skill_name, available_tools={alias})
                    assert spec.metadata.version == "1.1.0" and "release two" in spec.instructions
                    async with SessionFactory() as session:
                        await session.execute(
                            update(User).where(User.id == ids[1]).values(role="admin")
                        )
                        await session.commit()
                    denied = await ToolExecutor().execute(
                        new_runtime.registry, new_runtime.context, alias, {"value": "role changed"}
                    )
                    assert denied.is_error
                    async with SessionFactory() as session:
                        await session.execute(
                            update(User).where(User.id == ids[1]).values(role="user")
                        )
                        await session.commit()
                    assert (
                        await ToolExecutor().execute(
                            new_runtime.registry,
                            new_runtime.context,
                            alias,
                            {"value": "role restored"},
                        )
                    ).is_error
                async with plugin_runtime(
                    base, SkillRegistry(), replace(context, run_id=uuid4())
                ) as disabled:
                    await request(
                        admin,
                        "PATCH",
                        f"/api/admin/plugins/{plugin_id}/enabled",
                        {
                            "enabled": False,
                            "expected_catalog_version": (await state())["catalog_version"],
                        },
                    )
                    assert (
                        await ToolExecutor().execute(
                            disabled.registry, disabled.context, alias, {"value": "plugin disabled"}
                        )
                    ).is_error
                    await request(
                        admin,
                        "PATCH",
                        f"/api/admin/plugins/{plugin_id}/enabled",
                        {
                            "enabled": True,
                            "expected_catalog_version": (await state())["catalog_version"],
                        },
                    )
                    assert (
                        await ToolExecutor().execute(
                            disabled.registry,
                            disabled.context,
                            alias,
                            {"value": "plugin reenabled"},
                        )
                    ).is_error
                async with plugin_runtime(
                    base, SkillRegistry(), replace(context, run_id=uuid4())
                ) as new_runtime:
                    await enable(owner, connection_ids[0], False)
                    denied = await ToolExecutor().execute(
                        new_runtime.registry, new_runtime.context, alias, {"value": "disabled"}
                    )
                    assert denied.is_error
                return {
                    "dependency_grant_validation": True,
                    "owner_isolation": True,
                    "skill_graph_execution": True,
                    "chat_sse_bridge": True,
                    "revoke_blocks_cache": True,
                    "revoke_stops_active_graph": True,
                    "regrant_does_not_revive_run": True,
                    "checkpoint_generation_pinned": True,
                    "package_tamper_invalidates_cache": True,
                    "release_refresh_loads_new_instructions": True,
                    "connection_disable_blocks_call": True,
                    "role_generation_invalidates_run": True,
                    "global_disable_invalidates_run": True,
                    "llm_provider_calls": 0,
                }
    finally:
        if manager:
            await manager.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
        chat.get_langgraph_checkpointer = old_checkpointer
        settings.plugin_packages_config_path = old_path
        plugin_packages.PROJECT_ROOT = old_root
        async with SessionFactory() as session:
            await session.execute(delete(AuditLog).where(AuditLog.actor_user_id.in_(ids)))
            await session.execute(delete(Plugin).where(Plugin.id == plugin_id))
            await session.execute(delete(User).where(User.id.in_(ids)))
            await session.execute(
                update(PluginCatalogState)
                .where(PluginCatalogState.id == 1)
                .values(
                    revision=PluginCatalogState.revision + 1,
                    auth_version=PluginCatalogState.auth_version + 1,
                )
            )
            await session.commit()
        await engine.dispose()
        await close_langsmith_tracing()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check()), indent=2))
