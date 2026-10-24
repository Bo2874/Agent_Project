import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack, nullcontext
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT, TextContent, Tool

from agent_core.contracts import CapabilityDescriptor
from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolRegistry,
    ToolResult,
    create_context_tool,
)
from agent_core.tools.registry import RegisteredTool
from agent_core.tools.types import ToolAuthorizationError

_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True, slots=True)
class MCPToolBinding:
    """Reviewed host binding; annotations cannot weaken this execution policy."""

    remote_name: str
    local_name: str
    policy: ToolExecutionPolicy
    capability: CapabilityDescriptor | None = None


class MCPConnectionError(RuntimeError):
    """Raised when an MCP server cannot be connected or discovered."""


@dataclass(frozen=True, slots=True)
class MCPStdioConfig:
    """Trusted application configuration for one local MCP stdio server."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    tool_timeout_seconds: float = 10
    tool_max_retries: int = 1
    tool_retry_backoff_seconds: float = 0.2
    admin_only_tools: frozenset[str] = frozenset()
    approval_required_tools: frozenset[str] = frozenset()
    bindings: tuple[MCPToolBinding, ...] | None = None
    expected_remote_names: frozenset[str] | None = None
    expected_release: str | None = None
    startup_timeout_seconds: float = 30
    request_timeout_seconds: float = 30
    health_interval_seconds: float = 30
    health_timeout_seconds: float = 5
    shutdown_timeout_seconds: float = 15
    failure_threshold: int = 3
    restart_cooldown_seconds: float = 10
    max_discovery_pages: int = 32
    max_discovered_tools: int = 1024
    plugin_id: str | None = None
    manifest_fingerprint: str | None = None
    host_profile_fingerprint: str | None = None
    max_plugin_concurrency: int = 1
    protocol_mode: str = "auto"
    allowed_protocol_versions: tuple[str, ...] = ("2025-11-25", "2026-07-28")

    def __post_init__(self) -> None:
        if self.protocol_mode not in {"auto", "legacy"} or not self.allowed_protocol_versions:
            raise ValueError("Invalid MCP protocol policy")
        if not _NAMESPACE_PATTERN.fullmatch(self.name):
            raise ValueError("MCP server name chỉ gồm chữ thường, số và dấu gạch dưới")
        if not self.command.strip():
            raise ValueError("MCP command không được để trống")
        for value in (
            self.startup_timeout_seconds,
            self.request_timeout_seconds,
            self.health_interval_seconds,
            self.health_timeout_seconds,
            self.shutdown_timeout_seconds,
            self.restart_cooldown_seconds,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("MCP lifecycle limits must be finite and positive")
        if not 1 <= self.failure_threshold <= 10:
            raise ValueError("MCP failure threshold must be between 1 and 10")
        if not 1 <= self.max_plugin_concurrency <= 16:
            raise ValueError("Plugin concurrency must be between 1 and 16")
        if not 1 <= self.max_discovery_pages <= 100 or not 1 <= self.max_discovered_tools <= 4096:
            raise ValueError("MCP discovery limits are out of range")
        if self.bindings is not None:
            if not self.bindings or len({b.remote_name for b in self.bindings}) != len(
                self.bindings
            ):
                raise ValueError("MCP bindings must have unique remote names")
            if len({b.local_name for b in self.bindings}) != len(self.bindings):
                raise ValueError("MCP bindings must have unique local names")
        ToolExecutionPolicy(
            timeout_seconds=self.tool_timeout_seconds,
            max_retries=self.tool_max_retries,
            retry_backoff_seconds=self.tool_retry_backoff_seconds,
        )


class MCPClientAdapter:
    """Expose remote MCP tools through the application's ToolRegistry."""

    def __init__(self, config: MCPStdioConfig) -> None:
        self.config = config
        self._client: Client | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._registry: ToolRegistry | None = None
        self._registered_names: list[str] = []
        self._owned: tuple[RegisteredTool, ...] = ()
        self._call_lock = asyncio.Lock()
        self.on_failure: Callable[[str], None] | None = None
        self.plugin_semaphore: asyncio.Semaphore | None = None
        self._generation = 0
        self._discovery_fingerprint: str | None = None

    @property
    def protocol_version(self) -> str | None:
        return self._client.protocol_version if self._client else None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def connect_and_register(self, registry: ToolRegistry) -> tuple[BaseTool, ...]:
        if self.is_connected:
            raise MCPConnectionError(f"MCP server '{self.config.name}' đã kết nối")

        stack = AsyncExitStack()
        try:
            client = await self._open_client(stack)
            if client.protocol_version not in self.config.allowed_protocol_versions:
                raise MCPConnectionError("Negotiated MCP protocol is not approved")
            if self.config.expected_release is not None and (
                client.server_info is None
                or client.server_info.version != self.config.expected_release
            ):
                raise MCPConnectionError("MCP server release does not match its reviewed profile")
            remote_tools = await self._list_all_tools(client)
            tools = self._register_tools(registry, remote_tools)
        except BaseException as exc:
            await stack.aclose()
            if not isinstance(exc, Exception):
                raise
            if isinstance(exc, MCPConnectionError):
                raise
            raise MCPConnectionError(f"Không thể kết nối MCP server '{self.config.name}'") from exc

        self._client = client
        self._exit_stack = stack
        self._registry = registry
        return tools

    async def _open_client(self, stack: AsyncExitStack) -> Client:
        params = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            cwd=self.config.cwd,
        )
        return await stack.enter_async_context(
            Client(
                params,
                read_timeout_seconds=self.config.startup_timeout_seconds,
                mode=self.config.protocol_mode,
            )
        )

    async def close(self) -> None:
        self.detach()
        if self._exit_stack is not None:
            stack, self._exit_stack = self._exit_stack, None
            await stack.aclose()

    def detach(self) -> None:
        """Block new calls and atomically withdraw this server's registrations."""
        if self._registry is not None:
            self._registry.unregister_owned(self._owned)
        self._owned = ()
        self._registered_names.clear()
        self._registry = None
        self._client = None
        self._generation += 1
        self._discovery_fingerprint = None

    async def refresh_discovery(self) -> tuple[BaseTool, ...]:
        """Explicit bounded refresh; no transport context is entered/exited by this task."""
        client, registry = self._client, self._registry
        if client is None or registry is None:
            raise MCPConnectionError("MCP server is not connected")
        async with asyncio.timeout(self.config.startup_timeout_seconds):
            async with self._call_lock:
                remote = await self._list_all_tools(client, bypass=True)
                if self._client is not client or self._registry is not registry:
                    raise MCPConnectionError("MCP discovery owner is stale")
                return self._register_tools(registry, remote, replacing=True)

    async def healthcheck(self) -> bool | None:
        client = self._client
        if client is None:
            return False
        if self._call_lock.locked():
            return None  # Busy is not a failed liveness check.
        try:
            async with asyncio.timeout(self.config.health_timeout_seconds):
                async with self._call_lock:
                    await client.list_tools(cache_mode="bypass")
            return True
        except Exception:
            return False

    async def _list_all_tools(self, client: Client, *, bypass: bool = False) -> list[Tool]:
        tools: list[Tool] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(self.config.max_discovery_pages):
            result = await client.list_tools(
                cursor=cursor, **({"cache_mode": "bypass"} if bypass else {})
            )
            tools.extend(result.tools)
            if len(tools) > self.config.max_discovered_tools:
                raise MCPConnectionError("MCP discovery tool limit exceeded")
            if result.next_cursor is None:
                names = [tool.name for tool in tools]
                if len(names) != len(set(names)):
                    raise MCPConnectionError("MCP discovery returned duplicate tool names")
                return tools
            if result.next_cursor in seen:
                raise MCPConnectionError("MCP discovery cursor repeated")
            seen.add(result.next_cursor)
            cursor = result.next_cursor
        raise MCPConnectionError("MCP discovery page limit exceeded")

    def _register_tools(
        self,
        registry: ToolRegistry,
        remote_tools: list[Tool],
        *,
        replacing: bool = False,
    ) -> tuple[BaseTool, ...]:
        tools: list[BaseTool] = []
        staged = ToolRegistry()
        bindings = {b.remote_name: b for b in self.config.bindings or ()}
        expected = self.config.expected_remote_names
        if bindings:
            expected = frozenset(bindings)
        if expected is not None:
            if not expected.issubset({tool.name for tool in remote_tools}):
                raise MCPConnectionError("MCP server is missing declared tools")
            remote_tools = [tool for tool in remote_tools if tool.name in expected]
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    tool.model_dump(mode="json")
                    for tool in sorted(remote_tools, key=lambda t: t.name)
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if replacing and fingerprint == self._discovery_fingerprint:
            return tuple(entry.tool for entry in self._owned)
        generation = self._generation + 1
        try:
            for remote_tool in remote_tools:
                binding = bindings.get(remote_tool.name)
                local_name = (
                    binding.local_name if binding else f"{self.config.name}__{remote_tool.name}"
                )
                tool = create_context_tool(
                    name=local_name,
                    description=remote_tool.description
                    or f"MCP tool {remote_tool.name} from {self.config.name}",
                    args_schema=remote_tool.input_schema,
                    coroutine=self._make_handler(remote_tool.name, generation=generation),
                )
                annotations = remote_tool.annotations
                policy = (
                    binding.policy
                    if binding
                    else ToolExecutionPolicy(
                        timeout_seconds=self.config.tool_timeout_seconds,
                        max_retries=self.config.tool_max_retries,
                        retry_backoff_seconds=self.config.tool_retry_backoff_seconds,
                        idempotent=bool(annotations and annotations.idempotent_hint),
                        allowed_roles=(
                            frozenset({"admin"})
                            if remote_tool.name in self.config.admin_only_tools
                            else frozenset({"user", "admin"})
                        ),
                        requires_approval=(
                            remote_tool.name in self.config.approval_required_tools
                            or bool(annotations and annotations.destructive_hint)
                        ),
                    )
                )
                staged.register(
                    tool,
                    policy=policy,
                    capability=binding.capability if binding else None,
                    manifest_fingerprint=self.config.manifest_fingerprint,
                    host_profile_fingerprint=self.config.host_profile_fingerprint,
                )
                tools.append(tool)
            entries = tuple(staged.get(tool.name) for tool in tools)
            if replacing:
                registry.replace_owned(self._owned, entries)
            else:
                registry.register_batch(entries)
            self._owned = tuple(registry.get(tool.name) for tool in tools)
            self._registered_names = [tool.name for tool in tools]
            self._generation = generation
            self._discovery_fingerprint = fingerprint
        except Exception:
            self._registered_names.clear()
            raise
        return tuple(tools)

    def _make_handler(self, remote_name: str, *, generation: int | None = None):
        expected_generation = self._generation if generation is None else generation
        binding = next(
            (b for b in self.config.bindings or () if b.remote_name == remote_name), None
        )
        local_name = binding.local_name if binding else f"{self.config.name}__{remote_name}"

        async def handler(_: ToolContext, arguments: Mapping[str, Any]) -> ToolResult:
            if self._client is not None and self._generation != expected_generation:
                return ToolResult.failure(
                    "Discovery changed; start a new run", error_code="mcp_discovery_changed"
                )
            if not self._context_allowed(_):
                return ToolResult.failure(
                    "Connection owner mismatch", error_code="tool_permission_denied"
                )
            client = self._client
            if client is None:
                return ToolResult.failure(
                    "MCP server chưa kết nối",
                    error_code="mcp_not_connected",
                )

            sent = False
            try:
                async with asyncio.timeout(self.config.request_timeout_seconds):
                    async with self.plugin_semaphore or nullcontext():
                        async with self._call_lock:
                            if (
                                self._client is not client
                                or self._generation != expected_generation
                            ):
                                return ToolResult.failure(
                                    "MCP server unavailable", error_code="mcp_not_connected"
                                )
                            if isinstance(_, ToolContext) and _.authorization_guard is not None:
                                await _.authorization_guard(local_name)
                            sent = True
                            result = await client.call_tool(
                                remote_name,
                                dict(arguments),
                                read_timeout_seconds=self.config.request_timeout_seconds,
                            )
            except ToolAuthorizationError as exc:
                return ToolResult.failure(str(exc), error_code=exc.code)
            except Exception as exc:
                timed_out = isinstance(exc, TimeoutError) or (
                    isinstance(exc, MCPError) and exc.code == REQUEST_TIMEOUT
                )
                code = "tool_timeout" if timed_out else "mcp_protocol_error"
                if self.on_failure and sent:
                    self.on_failure(code)
                return ToolResult.failure(
                    "MCP request failed" if isinstance(exc, MCPError) else "MCP server unavailable",
                    error_code=code,
                )

            text_parts = [item.text for item in result.content if isinstance(item, TextContent)]
            content = "\n".join(text_parts).strip()
            structured = result.structured_content
            if not content and structured is not None:
                content = json.dumps(structured, ensure_ascii=False, default=str)
            if not content:
                content = "MCP tool đã trả về kết quả không phải văn bản."

            data: Mapping[str, Any]
            if isinstance(structured, Mapping):
                data = dict(structured)
            elif structured is None:
                data = {}
            else:
                data = {"structured_content": structured}

            if result.is_error:
                return ToolResult.failure(content, error_code="mcp_tool_error", data=data)
            return ToolResult.success(content, data=data)

        return handler

    def _context_allowed(self, context: ToolContext) -> bool:
        return True
