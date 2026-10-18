import asyncio
from uuid import uuid4

import pytest

from agent_core.graph import ToolCallRequest
from agent_core.tools import ToolContext
from backend.services.tool_approvals import (
    ToolApprovalForbiddenError,
    ToolApprovalManager,
)


def make_context() -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        conversation_id=uuid4(),
        user_role="user",
        run_id=uuid4(),
    )


def make_request() -> ToolCallRequest:
    return ToolCallRequest(
        id="call_approval",
        name="sensitive_tool",
        arguments={"action": "demo"},
        raw_arguments='{"action":"demo"}',
    )


async def test_approval_manager_accepts_owner_decision_once() -> None:
    manager = ToolApprovalManager(timeout_seconds=1)
    context = make_context()
    approval_id = uuid4()
    wait_task = asyncio.create_task(
        manager.wait_for_decision(context, str(approval_id), make_request())
    )
    await asyncio.sleep(0)

    manager.resolve(
        approval_id=approval_id,
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        approved=True,
    )

    assert await wait_task is True


async def test_approval_manager_rejects_another_user() -> None:
    manager = ToolApprovalManager(timeout_seconds=1)
    context = make_context()
    approval_id = uuid4()
    wait_task = asyncio.create_task(
        manager.wait_for_decision(context, str(approval_id), make_request())
    )
    await asyncio.sleep(0)

    with pytest.raises(ToolApprovalForbiddenError):
        manager.resolve(
            approval_id=approval_id,
            run_id=context.run_id,
            conversation_id=context.conversation_id,
            user_id=uuid4(),
            approved=True,
        )

    manager.resolve(
        approval_id=approval_id,
        run_id=context.run_id,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        approved=False,
    )
    assert await wait_task is False
