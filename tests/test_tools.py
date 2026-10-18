from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from agent_core.tools import (
    DuplicateToolError,
    ToolContext,
    ToolExecutionPolicy,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    create_context_tool,
)


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


async def echo_handler(context: ToolContext, arguments: Mapping[str, Any]) -> ToolResult:
    return ToolResult.success(
        f"{context.user_id}: {arguments['text']}",
        data={"text": arguments["text"]},
    )


def make_echo_tool() -> StructuredTool:
    return create_context_tool(
        name="echo_text",
        description="Trả lại đoạn văn bản được cung cấp.",
        args_schema=EchoInput,
        coroutine=echo_handler,
    )


async def test_registry_registers_and_returns_langchain_tool() -> None:
    registry = ToolRegistry()
    tool = make_echo_tool()
    registry.register(tool)
    context = ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user")

    registered = registry.get("echo_text")
    result = await registered.tool.ainvoke(
        {"text": "xin chào"},
        config={"configurable": {"tool_context": context}},
    )

    assert len(registry) == 1
    assert "echo_text" in registry
    assert registry.list_tools() == (tool,)
    assert result == ToolResult.success(f"{context.user_id}: xin chào", data={"text": "xin chào"})


def test_registry_rejects_duplicate_tool_name() -> None:
    registry = ToolRegistry()
    registry.register(make_echo_tool())

    with pytest.raises(DuplicateToolError, match="echo_text"):
        registry.register(make_echo_tool())


def test_registry_reports_unknown_tool() -> None:
    with pytest.raises(ToolNotFoundError, match="missing_tool"):
        ToolRegistry().get("missing_tool")


def test_registry_filters_allowlist_and_role_before_model_sees_tools() -> None:
    registry = ToolRegistry(allowlist={"echo_text"})
    registry.register(make_echo_tool())
    registry.register(
        create_context_tool(
            name="admin_tool",
            description="Chỉ admin được dùng.",
            args_schema={"type": "object", "properties": {}},
            coroutine=echo_handler,
        ),
        policy=ToolExecutionPolicy(allowed_roles=frozenset({"admin"})),
    )

    assert [tool.name for tool in registry.list_accessible_base_tools("user")] == ["echo_text"]
    assert [tool.name for tool in registry.list_accessible_base_tools("admin")] == ["echo_text"]


@pytest.mark.parametrize("name", ["Echo", "1_echo", "echo-text", "echo text"])
def test_tool_factory_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="Tool name"):
        create_context_tool(
            name=name,
            description="Mô tả hợp lệ",
            args_schema=EchoInput,
            coroutine=echo_handler,
        )


def test_tool_factory_requires_object_input_schema() -> None:
    with pytest.raises(ValueError, match="type='object'"):
        create_context_tool(
            name="bad_schema",
            description="Mô tả hợp lệ",
            args_schema={"type": "string"},
            coroutine=echo_handler,
        )


def test_tool_factory_rejects_invalid_json_schema() -> None:
    with pytest.raises(ValueError, match="JSON Schema"):
        create_context_tool(
            name="bad_schema",
            description="Mô tả hợp lệ",
            args_schema={
                "type": "object",
                "properties": {"count": {"type": "not-a-json-schema-type"}},
            },
            coroutine=echo_handler,
        )


def test_registry_requires_pydantic_args_schema() -> None:
    async def raw(**_: Any) -> ToolResult:
        return ToolResult.success("ok")

    tool = StructuredTool(
        name="raw_tool",
        description="Raw schema",
        args_schema={"type": "object", "properties": {}},
        coroutine=raw,
    )

    with pytest.raises(TypeError, match="Pydantic"):
        ToolRegistry().register(tool)


def test_tool_result_distinguishes_success_and_failure() -> None:
    success = ToolResult.success("Đã xử lý", data={"count": 1})
    failure = ToolResult.failure("Không thể xử lý", error_code="tool_failed")

    assert success.is_error is False
    assert success.error_code is None
    assert failure.is_error is True
    assert failure.error_code == "tool_failed"
