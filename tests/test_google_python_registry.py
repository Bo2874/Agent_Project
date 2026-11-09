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
