"""Read reviewed host profiles; chat, DB manifests and API bodies cannot launch commands."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from agent_core.contracts.base import Identifier
from agent_core.mcp import MCPStdioConfig
from agent_core.mcp.client import MCPToolBinding
from agent_core.plugins import load_plugin_manifest, tool_alias
from agent_core.plugins.manifest import PluginVersion
from agent_core.plugins.namespace import ServerId
from agent_core.tools import ToolExecutionPolicy
from backend.core.config import PROJECT_ROOT, Settings

Seconds = Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)]


class HostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class StdioProfile(HostConfig):
    id: Identifier
    release: PluginVersion
    command: Annotated[str, Field(min_length=1, max_length=1024)]
    args: Annotated[tuple[str, ...], Field(max_length=128)] = ()
    cwd: str = "@project"
    startup_timeout_seconds: Seconds = 30
    health_interval_seconds: Seconds = 30
    health_timeout_seconds: Seconds = 5
    shutdown_timeout_seconds: Seconds = 15
    failure_threshold: Annotated[int, Field(strict=True, ge=1, le=10)] = 3
    restart_cooldown_seconds: Seconds = 10

    @model_validator(mode="after")
    def validate_process(self):
        if self.command != "@python" and not Path(self.command).is_absolute():
            raise ValueError("Use @python or an absolute reviewed executable path")
        if self.cwd != "@project" and not Path(self.cwd).is_absolute():
            raise ValueError("Use @project or an absolute working directory")
        if any("\x00" in value or len(value) > 4096 for value in self.args):
            raise ValueError("Invalid stdio argument")
        return self


class StdioEntry(HostConfig):
    manifest: str
    server_id: ServerId
    enabled: StrictBool = False
    legacy_aliases: StrictBool = False


class StdioConfiguration(HostConfig):
    schema_version: Literal["1.0"]
    profiles: Annotated[tuple[StdioProfile, ...], Field(max_length=64)]
    servers: Annotated[tuple[StdioEntry, ...], Field(max_length=64)]

    @model_validator(mode="after")
    def unique_profiles(self):
        if len({profile.id for profile in self.profiles}) != len(self.profiles):
            raise ValueError("Duplicate stdio profile id")
        return self


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key")
        result[key] = value
    return result


def load_stdio_configs(settings: Settings) -> tuple[MCPStdioConfig, ...]:
    """All-or-nothing configuration validation before any server is launched."""
    try:
        path = settings.mcp_stdio_config_path
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        with path.open("rb") as stream:
            data = stream.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise ValueError("Stdio configuration size limit exceeded")
        config = StdioConfiguration.model_validate(json.loads(data, object_pairs_hook=_object))
        profiles = {profile.id: profile for profile in config.profiles}
        configs = []
        names = set()
        for entry in config.servers:
            manifest_path = (PROJECT_ROOT / entry.manifest).resolve()
            if not manifest_path.is_relative_to((PROJECT_ROOT / "plugins/manifests").resolve()):
                raise ValueError("Manifest must resolve inside plugins/manifests")
            manifest = load_plugin_manifest(manifest_path)
            server = next(item for item in manifest.servers if item.server_id == entry.server_id)
            profile = profiles[server.connection_profile_ref]
            if server.transport != "stdio" or server.credentials:
                raise ValueError("Only credential-free stdio host profiles are supported")
            if profile.release != server.release:
                raise ValueError("Profile release does not match manifest pin")
            legacy = entry.legacy_aliases
            if legacy and (manifest.id, manifest.namespace, server.server_id) != (
                "builtin.demo",
                "demo",
                "main",
            ):
                raise ValueError("Legacy aliases are reserved for the bundled demo")
            remote_names = frozenset(tool.remote_name for tool in server.tools)
            name = "demo" if legacy else f"{manifest.namespace}__{server.server_id}"
            if name in names:
                raise ValueError("Duplicate runtime server identity")
            names.add(name)
            if not entry.enabled or (legacy and not settings.mcp_demo_enabled):
                continue
            if not legacy and not settings.v2_features.is_enabled("plugins"):
                continue
            bindings = (
                None
                if legacy
                else tuple(
                    MCPToolBinding(
                        remote_name=tool.remote_name,
                        local_name=tool_alias(manifest.namespace, server.server_id, tool.name),
                        policy=ToolExecutionPolicy(
                            timeout_seconds=tool.policy.timeout_seconds,
                            max_retries=tool.policy.max_retries,
                            retry_backoff_seconds=tool.policy.retry_backoff_seconds,
                            idempotent=tool.policy.idempotent,
                            allowed_roles=frozenset(tool.policy.allowed_roles),
                            requires_approval=tool.policy.requires_approval,
                        ),
                        capability=next(
                            cap
                            for cap in manifest.capabilities()
                            if cap.kind == "tool"
                            and cap.name == tool.name
                            and cap.capability_id
                            == f"{manifest.namespace}.tool.{server.server_id}.{tool.name}"
                        ),
                    )
                    for tool in server.tools
                )
            )
            configs.append(
                MCPStdioConfig(
                    name=name,
                    command=sys.executable if profile.command == "@python" else profile.command,
                    args=profile.args,
                    cwd=str(PROJECT_ROOT) if profile.cwd == "@project" else profile.cwd,
                    bindings=bindings,
                    expected_remote_names=remote_names,
                    expected_release=profile.release,
                    tool_timeout_seconds=settings.tool_timeout_seconds,
                    tool_max_retries=settings.tool_max_retries,
                    tool_retry_backoff_seconds=settings.tool_retry_backoff_seconds,
                    admin_only_tools=frozenset(
                        tool.remote_name
                        for tool in server.tools
                        if "user" not in tool.policy.allowed_roles
                    ),
                    approval_required_tools=frozenset(
                        tool.remote_name for tool in server.tools if tool.policy.requires_approval
                    ),
                    request_timeout_seconds=server.request_timeout_seconds,
                    plugin_id=manifest.id,
                    manifest_fingerprint=manifest.fingerprint(),
                    host_profile_fingerprint=hashlib.sha256(
                        profile.model_dump_json().encode()
                    ).hexdigest(),
                    max_plugin_concurrency=manifest.max_concurrency,
                    **profile.model_dump(exclude={"id", "release", "command", "args", "cwd"}),
                )
            )
        return tuple(configs)
    except (ValueError, OSError, KeyError, StopIteration):
        raise ValueError(
            "Invalid MCP host configuration; review profiles and manifest bindings"
        ) from None
