from uuid import uuid4

import pytest

from agent_core.tools import ToolRegistry
from agent_core.tools.python import register_python_tool
from backend.schemas.automation import GrantDraft


def test_google_work_has_one_general_python_tool_without_approval():
    registry = ToolRegistry()
    register_python_tool(registry, None)
    assert [entry.tool.name for entry in registry.list_accessible_tools("user")] == [
        "run_python_code"
    ]
    assert not registry.get("run_python_code").policy.requires_approval
    from backend.services.google.context import GoogleContextService

    assert not hasattr(GoogleContextService, "invoke")


def test_native_sheet_edit_requires_explicit_whole_file_grant():
    grant = GrantDraft(connection_id=uuid4(), file_id="sheet-a", action="edit_sheet")
    assert grant.range_a1 is None
    with pytest.raises(ValueError):
        GrantDraft(connection_id=uuid4(), file_id="sheet-a", action="update_range")
    with pytest.raises(ValueError):
        GrantDraft(connection_id=uuid4(), file_id="sheet-a", action="edit_sheet", range_a1="A1:B10")


async def test_old_checkpoint_google_call_retires_without_approval_or_execution():
    from agent_core.tools import ToolContext, ToolExecutor
    from backend.services.google.legacy import checkpoint_registry

    registry = ToolRegistry()
    register_python_tool(registry, None)
    saved = {
        "loaded_tool_names": ["google_sheets_append"],
        "pending_tool_calls": [
            {"name": "google_sheets_append", "arguments": {"proposal_id": str(uuid4())}}
        ],
    }
    resumed = checkpoint_registry(registry, saved)
    assert "google_sheets_append" not in registry
    context = ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user")
    executor = ToolExecutor()
    assert not executor.needs_approval(resumed, context, "google_sheets_append", {})
    result = await executor.execute(
        resumed, context, "google_sheets_append", {"proposal_id": str(uuid4())}
    )
    assert result.error_code == "legacy_google_tools_retired"
