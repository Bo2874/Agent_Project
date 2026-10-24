"""Versioned application manifests; validation never installs, authorizes or starts a plugin."""

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StringConstraints, model_validator

from agent_core.contracts import CapabilityDescriptor
from agent_core.contracts.base import (
    ContractModel,
    Identifier,
    ShortText,
    SideEffect,
    require_unique,
)
from agent_core.plugins.namespace import (
    LocalName,
    PluginNamespace,
    ServerId,
    skill_capability_id,
    tool_alias,
    tool_capability_id,
)

MAX_MANIFEST_BYTES = 256 * 1024
# SemVer 2.0.0, including strict numeric prerelease identifiers.
_NUM = r"(?:0|[1-9][0-9]*)"
_PRE = rf"(?:{_NUM}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
PluginVersion = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=80,
        pattern=rf"^{_NUM}\.{_NUM}\.{_NUM}(?:-{_PRE}(?:\.{_PRE})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$",
    ),
]
RemoteToolName = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
]
_Concurrency = Annotated[int, Field(strict=True, ge=1, le=16)]
_Roles = Annotated[tuple[Literal["user", "admin"], ...], Field(min_length=1, max_length=2)]


class AccessPolicy(ContractModel):
    """Policy declared by the trusted host; remote MCP annotations are not policy."""

    allowed_roles: _Roles = ("admin",)
    side_effect: SideEffect = "unknown"
    requires_approval: StrictBool = True

    @model_validator(mode="after")
    def validate_access(self) -> Self:
        require_unique(self.allowed_roles, "allowed_roles")
        if self.side_effect != "read_only" and not self.requires_approval:
            raise ValueError("Write, destructive and unknown actions require approval")
        return self


class ToolPolicy(AccessPolicy):
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, le=300)] = 10.0
    max_retries: Annotated[int, Field(strict=True, ge=0, le=3)] = 0
    retry_backoff_seconds: Annotated[float, Field(strict=True, ge=0, le=10)] = 0.2
    idempotent: StrictBool = False
    max_concurrency: _Concurrency = 1

    @model_validator(mode="after")
    def validate_retry(self) -> Self:
        if self.max_retries and (not self.idempotent or self.side_effect != "read_only"):
            raise ValueError("Automatic retry requires an explicitly idempotent read-only tool")
        return self


class PluginTool(ContractModel):
    name: LocalName
    remote_name: RemoteToolName
    description: ShortText
    tags: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()
    input_schema_ref: Identifier | None = None
    output_schema_ref: Identifier | None = None
    policy: ToolPolicy = Field(default_factory=ToolPolicy)

    @model_validator(mode="after")
    def validate_tags(self) -> Self:
        require_unique(self.tags, "tool.tags")
        return self


class CredentialRequirement(ContractModel):
    """A logical connection-scoped slot, never a credential or per-user secret identifier."""

    slot: Identifier
    kind: Literal["api_key", "bearer_token", "oauth2"]


class ServerManifest(ContractModel):
    server_id: ServerId
    transport: Literal["stdio", "streamable_http"]
    release: PluginVersion
    connection_profile_ref: Identifier
    credentials: Annotated[tuple[CredentialRequirement, ...], Field(max_length=8)] = ()
    max_concurrency: _Concurrency = 1
    request_timeout_seconds: Annotated[float, Field(strict=True, gt=0, le=300)] = 30.0
    tools: Annotated[tuple[PluginTool, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def validate_server(self) -> Self:
        require_unique(tuple(tool.name for tool in self.tools), "server.tools.name")
        require_unique(tuple(tool.remote_name for tool in self.tools), "server.tools.remote_name")
        require_unique(tuple(item.slot for item in self.credentials), "server.credentials.slot")
        for tool in self.tools:
            if tool.policy.max_concurrency > self.max_concurrency:
                raise ValueError("Tool concurrency cannot exceed its server concurrency")
            if tool.policy.timeout_seconds > self.request_timeout_seconds:
                raise ValueError("Tool timeout cannot exceed its server request timeout")
        return self


class ToolReference(ContractModel):
    server_id: ServerId
    tool_name: LocalName


class PluginSkill(ContractModel):
    name: LocalName
    version: PluginVersion
    description: ShortText
    relative_directory: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
    tags: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()
    required_tools: Annotated[tuple[ToolReference, ...], Field(max_length=50)] = ()
    policy: AccessPolicy = Field(default_factory=AccessPolicy)

    @model_validator(mode="after")
    def validate_directory(self) -> Self:
        path = self.relative_directory
        # Lexical only. The package loader must check realpath/symlinks at Day 85.
        if (
            not re.fullmatch(r"[a-zA-Z0-9_./-]+", path)
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise ValueError("Skill directory must be a portable relative path without traversal")
        require_unique(self.tags, "skill.tags")
        require_unique(
            tuple((ref.server_id, ref.tool_name) for ref in self.required_tools), "required_tools"
        )
        return self


class PluginManifest(ContractModel):
    id: Identifier
    namespace: PluginNamespace
    version: PluginVersion
    policy_version: PluginVersion
    name: ShortText
    description: ShortText
    tags: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()
    max_concurrency: Annotated[int, Field(strict=True, ge=1, le=64)] = 1
    servers: Annotated[tuple[ServerManifest, ...], Field(min_length=1, max_length=16)]
    skills: Annotated[tuple[PluginSkill, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        require_unique(self.tags, "plugin.tags")
        require_unique(tuple(server.server_id for server in self.servers), "servers.server_id")
        require_unique(tuple(skill.name for skill in self.skills), "skills.name")
        tools = {}
        for server in self.servers:
            if server.max_concurrency > self.max_concurrency:
                raise ValueError("Server concurrency cannot exceed plugin concurrency")
            for tool in server.tools:
                tools[(server.server_id, tool.name)] = tool
                tool_alias(self.namespace, server.server_id, tool.name)
        for skill in self.skills:
            for ref in skill.required_tools:
                tool = tools.get((ref.server_id, ref.tool_name))
                if tool is None:
                    raise ValueError(
                        "Skill required_tools must reference tools declared in this plugin"
                    )
                if not set(skill.policy.allowed_roles).issubset(tool.policy.allowed_roles):
                    raise ValueError("Skill roles cannot exceed its required tool roles")
                if tool.policy.requires_approval and not skill.policy.requires_approval:
                    raise ValueError("Skill cannot bypass a required tool approval")
                severity = {"read_only": 0, "write": 1, "destructive": 2, "unknown": 3}
                if severity[skill.policy.side_effect] < severity[tool.policy.side_effect]:
                    raise ValueError("Skill cannot understate a required tool side effect")
        return self

    def canonical_data(self) -> dict:
        """Normalize unordered declarations; formatting and registration order are not versions."""
        data = self.model_dump(mode="json")
        data["tags"].sort()
        for server in data["servers"]:
            server["credentials"].sort(key=lambda item: item["slot"])
            for tool in server["tools"]:
                tool["tags"].sort()
                tool["policy"]["allowed_roles"].sort()
            server["tools"].sort(key=lambda tool: tool["name"])
        data["servers"].sort(key=lambda server: server["server_id"])
        for skill in data["skills"]:
            skill["tags"].sort()
            skill["policy"]["allowed_roles"].sort()
            skill["required_tools"].sort(key=lambda ref: (ref["server_id"], ref["tool_name"]))
        data["skills"].sort(key=lambda skill: skill["name"])
        return data

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.canonical_data(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def policy_fingerprint(self) -> str:
        data = self.canonical_data()
        for key in ("name", "description", "tags", "version", "policy_version"):
            data.pop(key)
        for server in data["servers"]:
            for tool in server["tools"]:
                tool.pop("description")
                tool.pop("tags")
        for skill in data["skills"]:
            skill.pop("description")
            skill.pop("tags")
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        descriptors = []
        for server in self.servers:
            for tool in server.tools:
                descriptors.append(
                    CapabilityDescriptor(
                        capability_id=tool_capability_id(
                            self.namespace, server.server_id, tool.name
                        ),
                        kind="tool",
                        name=tool.name,
                        description=tool.description,
                        version=self.version,
                        plugin_id=self.id,
                        tags=tool.tags,
                        input_schema_ref=tool.input_schema_ref,
                        output_schema_ref=tool.output_schema_ref,
                        side_effect=tool.policy.side_effect,
                        requires_approval=tool.policy.requires_approval,
                    )
                )
        for skill in self.skills:
            descriptors.append(
                CapabilityDescriptor(
                    capability_id=skill_capability_id(self.namespace, skill.name),
                    kind="skill",
                    name=skill.name,
                    description=skill.description,
                    version=skill.version,
                    plugin_id=self.id,
                    tags=skill.tags,
                    required_capability_ids=tuple(
                        sorted(
                            tool_capability_id(self.namespace, ref.server_id, ref.tool_name)
                            for ref in skill.required_tools
                        )
                    ),
                    side_effect=skill.policy.side_effect,
                    requires_approval=skill.policy.requires_approval,
                )
            )
        return tuple(sorted(descriptors, key=lambda item: item.capability_id))


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Manifest JSON must not contain duplicate object keys")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("Manifest JSON must not contain non-finite numbers")


def load_plugin_manifest(path: str | Path) -> PluginManifest:
    with Path(path).open("rb") as file:
        raw = file.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest exceeds the 256 KiB limit")
    payload = json.loads(
        raw.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    return PluginManifest.model_validate(payload)
