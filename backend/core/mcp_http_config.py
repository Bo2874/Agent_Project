"""Reviewed HTTP profiles and preregistered OAuth endpoints; never derive endpoints from chat."""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, model_validator

from agent_core.contracts.base import Identifier
from agent_core.mcp.client import MCPToolBinding
from agent_core.mcp.http import MCPHTTPConfig
from agent_core.mcp.outbound import OutboundPolicy, canonical_url
from agent_core.plugins import load_plugin_manifest, tool_alias
from agent_core.plugins.manifest import PluginVersion
from agent_core.plugins.namespace import ServerId
from agent_core.tools import ToolExecutionPolicy
from backend.core.config import PROJECT_ROOT, Settings
from backend.core.mcp_config import HostConfig, Seconds, _object


class OAuthProfile(HostConfig):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None = None
    client_id: Annotated[str, Field(min_length=1, max_length=256)]
    redirect_uri: str
    scopes: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    require_response_issuer: StrictBool = True

    @model_validator(mode="after")
    def validate_endpoints(self):
        for url in (
            self.issuer,
            self.authorization_endpoint,
            self.token_endpoint,
            *([self.revocation_endpoint] if self.revocation_endpoint else []),
        ):
            canonical_url(url)
        redirect = urlsplit(self.redirect_uri)
        canonical_url(self.redirect_uri, allow_http=True)
        if redirect.scheme != "https" and redirect.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("OAuth callback must use HTTPS or explicit loopback")
        if any(
            not scope or len(scope) > 128 or any(c.isspace() for c in scope)
            for scope in self.scopes
        ):
            raise ValueError("Invalid OAuth scope")
        return self


class HTTPProfile(HostConfig):
    id: Identifier
    release: PluginVersion
    endpoint: str
    protocol_mode: Literal["auto", "legacy"] = "auto"
    allowed_protocol_versions: tuple[Literal["2025-11-25", "2026-07-28"], ...] = (
        "2025-11-25",
        "2026-07-28",
    )
    startup_timeout_seconds: Seconds = 30
    health_interval_seconds: Seconds = 30
    health_timeout_seconds: Seconds = 5
    shutdown_timeout_seconds: Seconds = 15
    failure_threshold: Annotated[int, Field(strict=True, ge=1, le=10)] = 3
    restart_cooldown_seconds: Seconds = 10
    credential_header: Literal["authorization", "x-api-key"] = "authorization"
    oauth: OAuthProfile | None = None

    @model_validator(mode="after")
    def validate_endpoint(self):
        canonical_url(self.endpoint)
        if not self.allowed_protocol_versions:
            raise ValueError("At least one reviewed protocol revision is required")
        return self


class HTTPEntry(HostConfig):
    manifest: str
    server_id: ServerId
    enabled: StrictBool = False


class HTTPConfiguration(HostConfig):
    schema_version: Literal["1.0"]
    profiles: Annotated[tuple[HTTPProfile, ...], Field(max_length=64)] = ()
    servers: Annotated[tuple[HTTPEntry, ...], Field(max_length=64)] = ()


def load_http_configuration(settings: Settings) -> HTTPConfiguration:
    try:
        path = settings.mcp_http_config_path
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        with Path(path).open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError("HTTP config too large")
        config = HTTPConfiguration.model_validate(json.loads(raw, object_pairs_hook=_object))
        if len({profile.id for profile in config.profiles}) != len(config.profiles):
            raise ValueError("Duplicate HTTP profile")
        return config
    except (OSError, ValueError):
        raise ValueError("Invalid reviewed MCP HTTP configuration") from None


def build_http_config(manifest, server, profile: HTTPProfile, **private) -> MCPHTTPConfig:
    if server.transport != "streamable_http" or server.release != profile.release:
        raise ValueError("HTTP profile does not match manifest")
    return MCPHTTPConfig(
        name=f"{manifest.namespace}__{server.server_id}",
        endpoint=profile.endpoint,
        plugin_id=manifest.id,
        manifest_fingerprint=manifest.fingerprint(),
        host_profile_fingerprint=hashlib.sha256(profile.model_dump_json().encode()).hexdigest(),
        max_plugin_concurrency=manifest.max_concurrency,
        request_timeout_seconds=server.request_timeout_seconds,
        expected_release=profile.release,
        bindings=tuple(
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
                    if cap.capability_id
                    == f"{manifest.namespace}.tool.{server.server_id}.{tool.name}"
                ),
            )
            for tool in server.tools
        ),
        **profile.model_dump(exclude={"id", "endpoint", "release", "oauth", "credential_header"}),
        **private,
    )


def load_http_configs(settings: Settings) -> tuple[MCPHTTPConfig, ...]:
    config = load_http_configuration(settings)
    profiles = {profile.id: profile for profile in config.profiles}
    result, names = [], set()
    try:
        for entry in config.servers:
            path = (PROJECT_ROOT / entry.manifest).resolve()
            if not path.is_relative_to((PROJECT_ROOT / "plugins/manifests").resolve()):
                raise ValueError("Invalid manifest path")
            manifest = load_plugin_manifest(path)
            server = next(s for s in manifest.servers if s.server_id == entry.server_id)
            profile = profiles[server.connection_profile_ref]
            if server.credentials or profile.oauth:
                raise ValueError("Private HTTP credentials cannot enter the shared host registry")
            runtime = build_http_config(manifest, server, profile)
            if runtime.name in names:
                raise ValueError("Duplicate HTTP server identity")
            names.add(runtime.name)
            if entry.enabled and settings.v2_features.is_enabled("plugins"):
                result.append(runtime)
    except (OSError, ValueError, StopIteration, KeyError):
        raise ValueError("Invalid HTTP manifest/profile binding") from None
    return tuple(result)


def profile_policy(profile: HTTPProfile) -> OutboundPolicy:
    oauth = profile.oauth
    urls = [profile.endpoint]
    if oauth:
        urls.extend([oauth.token_endpoint])
        if oauth.revocation_endpoint:
            urls.append(oauth.revocation_endpoint)
    return OutboundPolicy(tuple(urls))
