import sys
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from agent_core.code_execution import register_python_execution_tool
from agent_core.mcp import MCPClientAdapter, MCPStdioConfig
from agent_core.tools import ToolRegistry
from backend.core.config import get_settings

settings = get_settings()
tool_registry = ToolRegistry(allowlist=settings.tool_allowlist_names)
if settings.python_sandbox_enabled:
    register_python_execution_tool(tool_registry)
_mcp_clients: list[MCPClientAdapter] = []
PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def start_tool_connections() -> None:
    if not settings.mcp_demo_enabled or _mcp_clients:
        return

    client = MCPClientAdapter(
        MCPStdioConfig(
            name="demo",
            command=sys.executable,
            args=("-m", "agent_core.mcp.demo_server"),
            cwd=str(PROJECT_ROOT),
            tool_timeout_seconds=settings.tool_timeout_seconds,
            tool_max_retries=settings.tool_max_retries,
            tool_retry_backoff_seconds=settings.tool_retry_backoff_seconds,
            admin_only_tools=frozenset({"get_admin_runtime_info"}),
            approval_required_tools=frozenset({"simulate_sensitive_action"}),
            routing_keywords_by_tool={
                "get_server_time": frozenset(
                    {"thời gian", "mấy giờ", "ngày giờ", "utc", "server time"}
                ),
                "calculate": frozenset(
                    {"tính", "phép toán", "cộng", "trừ", "nhân", "chia", "calculate"}
                ),
                "count_text": frozenset({"đếm", "ký tự", "bao nhiêu từ", "số dòng", "count text"}),
                "convert_temperature": frozenset(
                    {"nhiệt độ", "độ c", "độ f", "celsius", "fahrenheit", "kelvin"}
                ),
                "simulate_sensitive_action": frozenset(
                    {"mô phỏng", "hành động nhạy cảm", "simulate sensitive"}
                ),
                "get_admin_runtime_info": frozenset(
                    {"runtime", "thông tin server", "phiên bản server"}
                ),
            },
        )
    )
    await client.connect_and_register(tool_registry)
    _mcp_clients.append(client)


async def close_tool_connections() -> None:
    while _mcp_clients:
        await _mcp_clients.pop().close()


def get_tool_registry() -> ToolRegistry:
    return tool_registry


ToolRegistryDependency = Annotated[ToolRegistry, Depends(get_tool_registry)]
