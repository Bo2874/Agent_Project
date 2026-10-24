"""Run-local plugin bridge. Fresh DB authority at model/cached call/retry/RPC boundaries."""

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from agent_core.plugins.manager import PluginManager
from agent_core.plugins.namespace import skill_capability_id, tool_alias, tool_capability_id
from agent_core.skills import SkillRegistry
from agent_core.tools import ToolContext, ToolRegistry
from agent_core.tools.types import ToolAuthorizationError
from backend.core.config import get_settings
from backend.core.mcp_http_config import build_http_config, load_http_configuration
from backend.core.plugin_packages import ReviewedPackages
from backend.database import SessionFactory
from backend.models import PluginConnection, PluginGrant, User
from backend.services import plugin_credentials as credentials
from backend.services import plugins as control


@dataclass(frozen=True, slots=True)
class ConnectionPin:
    connection_id: UUID
    plugin_id: str
    role: str
    stamp: str
    scope: tuple[str, ...]


async def connection_snapshot(session, user_id, connection_id):
    actor = await session.get(User, user_id, populate_existing=True)
    if actor is None or not actor.is_active:
        raise ToolAuthorizationError()
    connection = await control._owned_connection(session, actor, connection_id)
    plugin = await control._plugin(session, connection.plugin_id)
    grant = await session.scalar(
        select(PluginGrant)
        .where(
            PluginGrant.user_id == user_id,
            PluginGrant.plugin_id == plugin.id,
        )
        .execution_options(populate_existing=True)
    )
    manifest, refs = control._manifest(plugin), await control._references(session, connection)
    if (
        control.connection_status(plugin, grant, connection, manifest, actor.role, refs)
        != "configured"
    ):
        raise ToolAuthorizationError()
    scope = control._effective_scope(manifest, actor.role, grant.capability_ids)
    payload = (
        str(user_id),
        actor.role,
        str(actor.updated_at),
        manifest.fingerprint(),
        str(plugin.updated_at),
        str(connection.id),
        str(connection.updated_at),
        str(grant.id),
        str(grant.updated_at),
        scope,
        tuple((str(r.id), r.server_id, r.slot, r.provider, str(r.secret_id)) for r in refs),
    )
    stamp = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return ConnectionPin(connection.id, plugin.id, actor.role, stamp, scope), manifest, actor


class RunPluginRuntime:
    def __init__(self, base, skills, context):
        self.base = base
        self.registry = base.fork(include_plugins=False)
        self.registry.owner_user_id = context.user_id
        self.registry.authorization_guard = self.authorize_tool
        self.skills = skills.fork()
        self.original_context = context
        self.context = replace(context, authorization_guard=self.authorize_tool)
        self.tool_pins = {}
        self.http_profile_pins = {}
        self.upstream = {t.name: base.get(t.name) for t in self.registry.list_tools()}
        self.entries = {}
        self.manager = PluginManager(self.registry)
        self.packages = ReviewedPackages(get_settings().plugin_packages_config_path)
        self.packages_fingerprint = self.packages.fingerprint

    async def check_pin(self, pin: ConnectionPin, capability_id: str):
        try:
            if not get_settings().v2_features.is_enabled("plugins"):
                raise ToolAuthorizationError()
            async with SessionFactory() as session:
                await control._state(session)
                current, _, _ = await connection_snapshot(
                    session, self.original_context.user_id, pin.connection_id
                )
                if current != pin or capability_id not in current.scope:
                    raise ToolAuthorizationError()
        except ToolAuthorizationError:
            raise
        except Exception:
            raise ToolAuthorizationError() from None

    def check_entry(self, name):
        expected = self.entries.get(name)
        if expected is not None and (
            name not in self.registry or self.registry.get(name) is not expected
        ):
            raise ToolAuthorizationError("tool_catalog_changed")
        original = self.upstream.get(name)
        if original is not None and (name not in self.base or self.base.get(name) is not original):
            raise ToolAuthorizationError("tool_catalog_changed")

    async def authorize_tool(self, name):
        if self.original_context.authorization_guard:
            await self.original_context.authorization_guard(name)
        self.check_entry(name)
        if name in self.tool_pins:
            pin, capability_id = self.tool_pins[name]
            await self.check_pin(pin, capability_id)
        if name in self.http_profile_pins:
            profile_id, expected = self.http_profile_pins[name]
            try:
                current = next(
                    p
                    for p in load_http_configuration(get_settings()).profiles
                    if p.id == profile_id
                )
                if credentials.fingerprint(current) != expected:
                    raise ToolAuthorizationError("plugin_profile_changed")
            except Exception:
                raise ToolAuthorizationError("plugin_profile_changed") from None

    async def prepare(self):
        settings = get_settings()
        http = {p.id: p for p in load_http_configuration(settings).profiles}
        sources = []
        async with SessionFactory() as session:
            await control._state(session)
            connections = list(
                (
                    await session.scalars(
                        select(PluginConnection)
                        .where(
                            PluginConnection.user_id == self.original_context.user_id,
                            PluginConnection.enabled.is_(True),
                        )
                        .order_by(PluginConnection.id)
                        .limit(33)
                    )
                ).all()
            )
            if len(connections) > 32:
                raise ToolAuthorizationError("plugin_runtime_limit")
            for connection in connections:
                try:
                    pin, manifest, actor = await connection_snapshot(
                        session, self.original_context.user_id, connection.id
                    )
                except ToolAuthorizationError:
                    continue
                if pin.role != self.original_context.user_role:
                    raise ToolAuthorizationError()
                caps = {c.capability_id: c for c in manifest.capabilities()}
                for server in manifest.servers:
                    granted = tuple(
                        tool
                        for tool in server.tools
                        if (
                            tool_capability_id(manifest.namespace, server.server_id, tool.name)
                            in pin.scope
                            and self.registry.is_allowlisted(
                                tool_alias(manifest.namespace, server.server_id, tool.name)
                            )
                        )
                    )
                    if not granted:
                        continue
                    if server.transport == "stdio":
                        if server.credentials:
                            continue
                        for tool in granted:
                            name = tool_alias(manifest.namespace, server.server_id, tool.name)
                            cap_id = tool_capability_id(
                                manifest.namespace, server.server_id, tool.name
                            )
                            if not self.base.can_access(name, pin.role):
                                continue
                            entry = self.base.get(name)
                            if (
                                entry.capability != caps[cap_id]
                                or entry.manifest_fingerprint != manifest.fingerprint()
                            ):
                                continue
                            self.registry.register(
                                entry.tool,
                                policy=entry.policy,
                                routing=entry.routing,
                                capability=entry.capability,
                                manifest_fingerprint=entry.manifest_fingerprint,
                                host_profile_fingerprint=entry.host_profile_fingerprint,
                            )
                            self.upstream[name] = entry
                            self.tool_pins[name] = (pin, cap_id)
                    else:
                        if len(self.manager.health()) >= 64:
                            raise ToolAuthorizationError("plugin_runtime_limit")
                        if server.credentials:
                            config = await credentials.private_http_config(
                                session, actor, connection.id, server.server_id
                            )
                            permitted = {tool.remote_name for tool in granted}
                            config = replace(
                                config,
                                bindings=tuple(
                                    b for b in config.bindings if b.remote_name in permitted
                                ),
                            )
                        else:
                            profile = http.get(server.connection_profile_ref)
                            if profile is None or profile.oauth is not None:
                                continue
                            config = build_http_config(
                                manifest,
                                server.model_copy(update={"tools": granted}),
                                profile,
                                owner_user_id=self.original_context.user_id,
                            )
                        self.manager.add_server(config)
                        for tool in granted:
                            name = tool_alias(manifest.namespace, server.server_id, tool.name)
                            self.tool_pins[name] = (
                                pin,
                                tool_capability_id(manifest.namespace, server.server_id, tool.name),
                            )
                            self.http_profile_pins[name] = (
                                server.connection_profile_ref,
                                config.host_profile_fingerprint,
                            )
                for source in self.packages.sources(manifest):
                    cap_id = skill_capability_id(manifest.namespace, source.skill.name)
                    if cap_id in pin.scope and pin.role in source.skill.policy.allowed_roles:
                        source.check_metadata()
                        sources.append((source, pin, caps[cap_id]))
        # All DB read locks are released before startup/network I/O.
        await self.manager.start()
        self.entries = {t.name: self.registry.get(t.name) for t in self.registry.list_tools()}
        for source, pin, descriptor in sources:
            if not all(
                self.registry.can_access(name, pin.role) for name in source.metadata.required_tools
            ):
                continue

            async def validate_metadata(source=source, pin=pin, descriptor=descriptor):
                await self.check_pin(pin, descriptor.capability_id)
                try:
                    if not self.packages.current():
                        raise ToolAuthorizationError("plugin_package_changed")
                    source.check_metadata()
                    for name in source.metadata.required_tools:
                        self.check_entry(name)
                except ToolAuthorizationError:
                    raise
                except Exception:
                    raise ToolAuthorizationError("plugin_package_changed") from None

            async def validate(source=source, guard=validate_metadata):
                await guard()
                source.check()

            self.skills.register_loader(
                source.metadata,
                source.load,
                validator=validate,
                capability=descriptor,
                metadata_validator=validate_metadata,
            )
        if self.tool_pins or self.skills.capabilities():
            payload = {
                "tools": {
                    name: (pin.stamp, cap_id) for name, (pin, cap_id) in self.tool_pins.items()
                },
                "packages": self.packages.fingerprint,
                "profiles": {
                    name: entry.host_profile_fingerprint
                    for name, entry in self.entries.items()
                    if name in self.tool_pins
                },
            }
            self.context = replace(
                self.context,
                capability_version=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
            )


@asynccontextmanager
async def plugin_runtime(base: ToolRegistry, skills: SkillRegistry, context: ToolContext):
    if not get_settings().v2_features.is_enabled("plugins"):
        # Preserve the V1 path without DB, filesystem or transport work.
        yield SimpleNamespace(registry=base, skills=skills, context=context)
        return
    try:
        runtime = RunPluginRuntime(base, skills, context)
    except Exception:
        raise ToolAuthorizationError("plugin_package_unavailable") from None
    try:
        try:
            await runtime.prepare()
        except ToolAuthorizationError:
            raise
        except Exception:
            raise ToolAuthorizationError("plugin_runtime_unavailable") from None
        yield runtime
    finally:
        await runtime.manager.close()
