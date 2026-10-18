import asyncio
import json
import re
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent, Tool

from agent_core.tools import (
    ToolContext,
    ToolExecutionPolicy,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    create_context_tool,
)

_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,30}$")


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
    routing_keywords_by_tool: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NAMESPACE_PATTERN.fullmatch(self.name):
            raise ValueError("MCP server name chỉ gồm chữ thường, số và dấu gạch dưới")
        if not self.command.strip():
            raise ValueError("MCP command không được để trống")
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
        self._call_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def connect_and_register(self, registry: ToolRegistry) -> tuple[BaseTool, ...]:
        if self.is_connected:
            raise MCPConnectionError(f"MCP server '{self.config.name}' đã kết nối")

        stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            cwd=self.config.cwd,
        )
        try:
            client = await stack.enter_async_context(Client(params))
            remote_tools = await self._list_all_tools(client)
            tools = self._register_tools(registry, remote_tools)
        except Exception as exc:
            await stack.aclose()
            if isinstance(exc, MCPConnectionError):
                raise
            raise MCPConnectionError(f"Không thể kết nối MCP server '{self.config.name}'") from exc

        self._client = client
        self._exit_stack = stack
        self._registry = registry
        return tools

    async def close(self) -> None:
        if self._registry is not None:
            for name in reversed(self._registered_names):
                if name in self._registry:
                    self._registry.unregister(name)
        self._registered_names.clear()
        self._registry = None
        self._client = None

        if self._exit_stack is not None:
            stack, self._exit_stack = self._exit_stack, None
            await stack.aclose()

    async def _list_all_tools(self, client: Client) -> list[Tool]:
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            result = await client.list_tools(cursor=cursor)
            tools.extend(result.tools)
            if result.next_cursor is None:
                return tools
            cursor = result.next_cursor

    def _register_tools(
        self,
        registry: ToolRegistry,
        remote_tools: list[Tool],
    ) -> tuple[BaseTool, ...]:
        tools: list[BaseTool] = []
        try:
            for remote_tool in remote_tools:
                local_name = f"{self.config.name}__{remote_tool.name}"
                tool = create_context_tool(
                    name=local_name,
                    description=remote_tool.description
                    or f"MCP tool {remote_tool.name} from {self.config.name}",
                    args_schema=remote_tool.input_schema,
                    coroutine=self._make_handler(remote_tool.name),
                )
                annotations = remote_tool.annotations
                policy = ToolExecutionPolicy(
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
                registry.register(
                    tool,
                    policy=policy,
                    routing=ToolRoutingPolicy(
                        keywords=self.config.routing_keywords_by_tool.get(
                            remote_tool.name, frozenset()
                        )
                    ),
                )
                self._registered_names.append(local_name)
                tools.append(tool)
        except Exception:
            for name in reversed(self._registered_names):
                if name in registry:
                    registry.unregister(name)
            self._registered_names.clear()
            raise
        return tuple(tools)

    def _make_handler(self, remote_name: str):
        async def handler(_: ToolContext, arguments: Mapping[str, Any]) -> ToolResult:
            client = self._client
            if client is None:
                return ToolResult.failure(
                    "MCP server chưa kết nối",
                    error_code="mcp_not_connected",
                )

            try:
                async with self._call_lock:
                    result = await client.call_tool(remote_name, dict(arguments))
            except MCPError as exc:
                return ToolResult.failure(
                    f"MCP protocol error: {exc}",
                    error_code="mcp_protocol_error",
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
