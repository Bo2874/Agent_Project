from collections.abc import Collection
from dataclasses import dataclass

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from agent_core.tools.types import ToolExecutionPolicy, ToolRoutingPolicy


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


class ToolRegistry:
    """Application policies around executable LangChain tools."""

    def __init__(self, *, allowlist: Collection[str] | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._allowlist = frozenset(allowlist) if allowlist is not None else None

    def register(
        self,
        tool: BaseTool,
        *,
        policy: ToolExecutionPolicy | None = None,
        routing: ToolRoutingPolicy | None = None,
    ) -> None:
        if not isinstance(tool, BaseTool):
            raise TypeError("Tool phải là LangChain BaseTool")
        if not (isinstance(tool.args_schema, type) and issubclass(tool.args_schema, BaseModel)):
            raise TypeError("LangChain tool phải dùng Pydantic args_schema")
        if tool.name in self._tools:
            raise DuplicateToolError(f"Tool '{tool.name}' đã được đăng ký")
        self._tools[tool.name] = RegisteredTool(
            tool=tool,
            policy=policy or ToolExecutionPolicy(),
            routing=routing or ToolRoutingPolicy(),
        )

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Không tìm thấy tool '{name}'") from exc

    def unregister(self, name: str) -> RegisteredTool:
        try:
            return self._tools.pop(name)
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
