import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from agent_core.tools import (
    ContextToolCoroutine,
    ToolContext,
    ToolExecutionPolicy,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    create_context_tool,
)


def make_context() -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
        run_id=uuid4(),
    )


def make_tool(handler: ContextToolCoroutine):
    return create_context_tool(
        name="test_tool",
        description="Tool dùng cho test.",
        args_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        coroutine=handler,
    )


async def test_executor_rejects_arguments_before_calling_handler() -> None:
    calls = 0

    async def handler(_: ToolContext, __: Mapping[str, Any]) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.success("ok")

    registry = ToolRegistry()
    registry.register(make_tool(handler))

    result = await ToolExecutor().execute(
        registry,
        make_context(),
        "test_tool",
        {"value": "not-an-integer"},
    )

    assert result.is_error is True
    assert result.error_code == "tool_schema_validation_failed"
    assert calls == 0


async def test_executor_retries_transient_failure_for_idempotent_tool() -> None:
    calls = 0

    async def handler(_: ToolContext, __: Mapping[str, Any]) -> ToolResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ToolResult.failure("Tạm thời lỗi", error_code="temporary_failure")
        return ToolResult.success("ok", data={"attempt": calls})

    registry = ToolRegistry()
    registry.register(
        make_tool(handler),
        policy=ToolExecutionPolicy(
            idempotent=True,
            max_retries=1,
            retry_backoff_seconds=0,
        ),
    )

    result = await ToolExecutor().execute(
        registry,
        make_context(),
        "test_tool",
        {"value": 1},
    )

    assert result == ToolResult.success("ok", data={"attempt": 2})
    assert calls == 2


async def test_executor_does_not_retry_non_idempotent_tool() -> None:
    calls = 0

    async def handler(_: ToolContext, __: Mapping[str, Any]) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.failure("Tạm thời lỗi", error_code="temporary_failure")

    registry = ToolRegistry()
    registry.register(
        make_tool(handler),
        policy=ToolExecutionPolicy(
            idempotent=False,
            max_retries=3,
            retry_backoff_seconds=0,
        ),
    )

    result = await ToolExecutor().execute(
        registry,
        make_context(),
        "test_tool",
        {"value": 1},
    )

    assert result.error_code == "temporary_failure"
    assert calls == 1


async def test_executor_times_out_and_retries_within_policy() -> None:
    calls = 0

    async def handler(_: ToolContext, __: Mapping[str, Any]) -> ToolResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.03)
        return ToolResult.success("quá muộn")

    registry = ToolRegistry()
    registry.register(
        make_tool(handler),
        policy=ToolExecutionPolicy(
            timeout_seconds=0.005,
            idempotent=True,
            max_retries=1,
            retry_backoff_seconds=0,
        ),
    )

    result = await ToolExecutor().execute(
        registry,
        make_context(),
        "test_tool",
        {"value": 1},
    )

    assert result.is_error is True
    assert result.error_code == "tool_timeout"
    assert calls == 2


async def test_executor_reuses_successful_idempotent_result() -> None:
    calls = 0

    async def handler(_: ToolContext, arguments: Mapping[str, Any]) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.success("ok", data={"value": arguments["value"], "call": calls})

    registry = ToolRegistry()
    registry.register(
        make_tool(handler),
        policy=ToolExecutionPolicy(idempotent=True),
    )
    executor = ToolExecutor()
    context = make_context()

    first = await executor.execute(registry, context, "test_tool", {"value": 7})
    second = await executor.execute(registry, context, "test_tool", {"value": 7})

    assert first == second
    assert calls == 1


async def test_executor_enforces_allowlist_and_role() -> None:
    async def handler(_: ToolContext, __: Mapping[str, Any]) -> ToolResult:
        return ToolResult.success("ok")

    context = make_context()
    blocked_registry = ToolRegistry(allowlist=set())
    blocked_registry.register(make_tool(handler))
    blocked = await ToolExecutor().execute(
        blocked_registry,
        context,
        "test_tool",
        {"value": 1},
    )

    admin_registry = ToolRegistry()
    admin_registry.register(
        make_tool(handler),
        policy=ToolExecutionPolicy(allowed_roles=frozenset({"admin"})),
    )
    denied = await ToolExecutor().execute(
        admin_registry,
        context,
        "test_tool",
        {"value": 1},
    )

    assert blocked.error_code == "tool_not_allowlisted"
    assert denied.error_code == "tool_permission_denied"


async def test_executor_requires_explicit_approval() -> None:
    calls = 0

    async def handler(_: ToolContext, __: Mapping[str, Any]) -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult.success("approved")

    registry = ToolRegistry()
    registry.register(
        make_tool(handler),
        policy=ToolExecutionPolicy(requires_approval=True),
    )
    executor = ToolExecutor()
    context = make_context()

    missing = await executor.execute(registry, context, "test_tool", {"value": 1})
    denied = await executor.execute(
        registry,
        context,
        "test_tool",
        {"value": 1},
        approval_granted=False,
    )
    approved = await executor.execute(
        registry,
        context,
        "test_tool",
        {"value": 1},
        approval_granted=True,
    )

    assert missing.error_code == "tool_approval_required"
    assert denied.error_code == "tool_approval_denied"
    assert approved == ToolResult.success("approved")
    assert calls == 1
