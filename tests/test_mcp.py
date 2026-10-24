import sys
from pathlib import Path
from uuid import uuid4

import pytest

from agent_core.mcp import MCPClientAdapter, MCPStdioConfig
from agent_core.tools import ToolContext, ToolExecutor, ToolNotFoundError, ToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_mcp_stdio_server_discovers_registers_and_calls_tool() -> None:
    registry = ToolRegistry()
    adapter = MCPClientAdapter(
        MCPStdioConfig(
            name="demo",
            command=sys.executable,
            args=("-m", "agent_core.mcp.demo_server"),
            cwd=str(PROJECT_ROOT),
            admin_only_tools=frozenset({"get_admin_runtime_info"}),
            approval_required_tools=frozenset({"simulate_sensitive_action"}),
        )
    )

    tools = await adapter.connect_and_register(registry)
    try:
        assert adapter.is_connected
        assert [tool.name for tool in tools] == [
            "demo__get_server_time",
            "demo__calculate",
            "demo__count_text",
            "demo__convert_temperature",
            "demo__simulate_sensitive_action",
            "demo__get_admin_runtime_info",
        ]
        assert registry.get("demo__get_server_time").policy.idempotent is False
        assert registry.get("demo__calculate").policy.idempotent is True
        assert registry.get("demo__calculate").routing.keywords == frozenset()
        assert registry.get("demo__simulate_sensitive_action").policy.requires_approval is True
        assert registry.get("demo__get_admin_runtime_info").policy.allowed_roles == frozenset(
            {"admin"}
        )

        context = ToolContext(
            user_id=uuid4(),
            conversation_id=uuid4(),
            user_role="user",
        )
        executor = ToolExecutor()
        result = await executor.execute(registry, context, "demo__get_server_time", {})

        assert result.is_error is False
        assert result.data["timezone"] == "UTC"
        assert "iso_time" in result.data

        calculate_result = await executor.execute(
            registry,
            context,
            "demo__calculate",
            {"a": 12.5, "b": 4, "operation": "multiply"},
        )
        assert calculate_result.is_error is False
        assert calculate_result.data["result"] == 50

        sample_text = "Xin chào\nAI Agent"
        count_result = await executor.execute(
            registry,
            context,
            "demo__count_text",
            {"text": sample_text},
        )
        assert count_result.is_error is False
        assert count_result.data == {
            "characters": len(sample_text),
            "words": 4,
            "lines": 2,
        }

        temperature_result = await executor.execute(
            registry,
            context,
            "demo__convert_temperature",
            {"value": 0, "from_unit": "celsius", "to_unit": "fahrenheit"},
        )
        assert temperature_result.is_error is False
        assert temperature_result.data["result"] == 32
    finally:
        await adapter.close()

    assert not adapter.is_connected
    for tool in tools:
        with pytest.raises(ToolNotFoundError):
            registry.get(tool.name)
