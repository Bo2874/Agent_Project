import json
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel

from agent_core.capability_catalog import CapabilityCatalog, CapabilityRecord
from agent_core.contracts import CapabilityDescriptor
from agent_core.tools.types import (
    ToolAuthorizationError,
    ToolContext,
    ToolExecutionPolicy,
    ToolRoutingPolicy,
)


class ToolRegistryError(RuntimeError):
    """Base error raised by ToolRegistry."""


class DuplicateToolError(ToolRegistryError):
    pass


class ToolNotFoundError(ToolRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    tool: BaseTool
    policy: ToolExecutionPolicy
    routing: ToolRoutingPolicy
    capability: CapabilityDescriptor | None = None
    manifest_fingerprint: str | None = None
    host_profile_fingerprint: str | None = None


class ToolRegistry:
    """Application policies around executable LangChain tools."""

    def __init__(self, *, allowlist: Collection[str] | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._allowlist = frozenset(allowlist) if allowlist is not None else None
        self._revision = 0
        self._catalogs: dict[str, CapabilityCatalog] = {}
        self._schemas: dict[str, tuple[RegisteredTool, str]] = {}
        self.owner_user_id = None
        self.authorization_guard: Callable[[str], Awaitable[None]] | None = None

    async def authorize(self, context: ToolContext, name: str) -> None:
        if self.owner_user_id is not None and self.owner_user_id != context.user_id:
            raise ToolAuthorizationError("tool_permission_denied")
        if self.authorization_guard:
            await self.authorization_guard(name)

    def _changed(self) -> None:
        self._revision += 1
        self._catalogs.clear()
        self._schemas = {
            name: cached
            for name, cached in self._schemas.items()
            if self._tools.get(name) is cached[0]
        }

    def fork(self, *, include_plugins: bool = True) -> "ToolRegistry":
        """Run-local view retaining host allowlist and immutable registration identities."""
        result = ToolRegistry(allowlist=self._allowlist)
        result._tools = {
            name: entry
            for name, entry in self._tools.items()
            if include_plugins or entry.capability is None or entry.capability.plugin_id is None
        }
        result._revision = self._revision
        result.owner_user_id = self.owner_user_id
        result.authorization_guard = self.authorization_guard
        return result

    def capability_catalog(self, role: str) -> CapabilityCatalog:
        if role not in {"user", "admin"}:
            return CapabilityCatalog((), revision=self._revision)
        if role not in self._catalogs:
            records = tuple(
                CapabilityRecord(
                    descriptor=entry.capability
                    or CapabilityDescriptor(
                        capability_id=entry.tool.name,
                        kind="tool",
                        name=entry.tool.name,
                        description=entry.tool.description[:1000],
                        version="runtime1",
                    ),
                    binding_name=entry.tool.name,
                    keywords=entry.routing.keywords,
                    always_load=entry.routing.always_load,
                )
                for entry in self.list_accessible_tools(role)
            )
            self._catalogs[role] = CapabilityCatalog(records, revision=self._revision)
        return self._catalogs[role]

    def scoped(self, names: Collection[str]) -> "ToolRegistry":
        """Private worker view preserving registration identity and authorization."""
        result = self.fork()
        result._tools = {n: e for n, e in result._tools.items() if n in names}
        result._changed()
        return result

    def model_definition(self, name: str) -> dict:
        """Convert only selected schemas; return a fresh copy of the cached JSON."""
        entry = self.get(name)
        cached = self._schemas.get(name)
        if cached is None or cached[0] is not entry:
            # StructuredTool.tool_call_schema rebuilds dynamic Pydantic fields as Any,
            # losing the original MCP constraints. Preserve the validated JSON Schema.
            payload = json.dumps(
                convert_to_openai_tool(
                    {
                        "name": entry.tool.name,
                        "description": entry.tool.description,
                        "parameters": entry.tool.args_schema.model_json_schema(),
                    }
                ),
                sort_keys=True,
            )
            self._schemas[name] = (entry, payload)
        return json.loads(self._schemas[name][1])

    def register(
        self,
        tool: BaseTool,
        *,
        policy: ToolExecutionPolicy | None = None,
        routing: ToolRoutingPolicy | None = None,
        capability: CapabilityDescriptor | None = None,
        manifest_fingerprint: str | None = None,
        host_profile_fingerprint: str | None = None,
    ) -> None:
        if not isinstance(tool, BaseTool):
            raise TypeError("Tool phải là LangChain BaseTool")
        if not (isinstance(tool.args_schema, type) and issubclass(tool.args_schema, BaseModel)):
            raise TypeError("LangChain tool phải dùng Pydantic args_schema")
        if tool.name in self._tools:
            raise DuplicateToolError(f"Tool '{tool.name}' đã được đăng ký")
        if capability is not None and capability.kind != "tool":
            raise ValueError("An executable tool requires a tool capability descriptor")
        capability_id = capability.capability_id if capability else tool.name
        if len(self._tools) >= 4096 or any(
            (entry.capability.capability_id if entry.capability else entry.tool.name)
            == capability_id
            for entry in self._tools.values()
        ):
            raise ToolRegistryError("Capability identity collision or registry size limit")
        self._tools[tool.name] = RegisteredTool(
            tool=tool,
            policy=policy or ToolExecutionPolicy(),
            routing=routing or ToolRoutingPolicy(),
            capability=capability,
            manifest_fingerprint=manifest_fingerprint,
            host_profile_fingerprint=host_profile_fingerprint,
        )
        self._changed()

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Không tìm thấy tool '{name}'") from exc

    def register_batch(self, entries: Collection[RegisteredTool]) -> None:
        """Validate the whole batch before publishing a new registry snapshot."""
        pending = ToolRegistry()
        for entry in entries:
            pending.register(
                entry.tool,
                policy=entry.policy,
                routing=entry.routing,
                capability=entry.capability,
                manifest_fingerprint=entry.manifest_fingerprint,
                host_profile_fingerprint=entry.host_profile_fingerprint,
            )
            if entry.tool.name in self._tools:
                raise DuplicateToolError(f"Tool '{entry.tool.name}' đã được đăng ký")
        ids = [
            entry.capability.capability_id if entry.capability else entry.tool.name
            for entry in (*self._tools.values(), *entries)
        ]
        if len(ids) != len(set(ids)) or len(ids) > 4096:
            raise ToolRegistryError("Capability identity collision or registry size limit")
        self._tools = {**self._tools, **pending._tools}
        if entries:
            self._changed()

    def replace_owned(
        self, old: Collection[RegisteredTool], new: Collection[RegisteredTool]
    ) -> None:
        """Discovery publication is all-or-nothing and cannot overwrite another owner."""
        if any(self._tools.get(entry.tool.name) is not entry for entry in old):
            raise ToolRegistryError("Discovery owner is stale")
        candidate = ToolRegistry(allowlist=self._allowlist)
        removed = {entry.tool.name for entry in old}
        candidate._tools = {
            name: entry for name, entry in self._tools.items() if name not in removed
        }
        candidate.register_batch(new)
        self._tools = candidate._tools
        self._changed()

    def unregister_owned(self, entries: Collection[RegisteredTool]) -> None:
        """Remove only registrations still owned by this connection generation."""
        names = {entry.tool.name for entry in entries if self._tools.get(entry.tool.name) is entry}
        self._tools = {name: entry for name, entry in self._tools.items() if name not in names}
        if names:
            self._changed()

    def unregister(self, name: str) -> RegisteredTool:
        try:
            entry = self._tools.pop(name)
            self._changed()
            return entry
        except KeyError as exc:
            raise ToolNotFoundError(f"Không tìm thấy tool '{name}'") from exc

    def list_tools(self) -> tuple[BaseTool, ...]:
        return tuple(registered.tool for registered in self._tools.values())

    def is_allowlisted(self, name: str) -> bool:
        return self._allowlist is None or name in self._allowlist

    def is_role_allowed(self, name: str, role: str) -> bool:
        return role in self.get(name).policy.allowed_roles

    def can_access(self, name: str, role: str) -> bool:
        return (
            name in self._tools and self.is_allowlisted(name) and self.is_role_allowed(name, role)
        )

    def list_accessible_tools(self, role: str) -> tuple[RegisteredTool, ...]:
        return tuple(
            registered
            for name, registered in self._tools.items()
            if self.is_allowlisted(name) and role in registered.policy.allowed_roles
        )

    def list_accessible_base_tools(self, role: str) -> tuple[BaseTool, ...]:
        return tuple(registered.tool for registered in self.list_accessible_tools(role))

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
