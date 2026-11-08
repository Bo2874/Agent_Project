from uuid import uuid4

from agent_core.tools import ToolContext, ToolExecutor, ToolRegistry, ToolResult


async def test_python_is_general_and_does_not_request_approval():
    from agent_core.tools.python import register_python_tool

    class Service:
        async def execute(self, context, request):
            return ToolResult.success("computed", data={"code": request.code})

    registry = ToolRegistry()
    register_python_tool(registry, Service())
    entry = registry.get("run_python_code")
    assert not entry.policy.requires_approval
    assert entry.policy.timeout_seconds >= 150
    assert "Excel" in entry.tool.description
    assert "stdin" in entry.tool.description
    assert "fresh" in entry.tool.description
    assert "create_excel" not in entry.tool.description
    assert {item.tool.name for item in registry.list_accessible_tools("user")} == {
        "run_python_code"
    }
    schema = registry.model_definition("run_python_code")["function"]["parameters"]
    assert {"code", "stdin", "input_version_ids", "outputs", "primary_output"} <= schema[
        "properties"
    ].keys()
    context = ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user")
    executor = ToolExecutor(force_approval=True)
    assert not executor.needs_approval(registry, context, "run_python_code", {"code": "print(1)"})
    result = await executor.execute(registry, context, "run_python_code", {"code": "print(1)"})
    assert not result.is_error


def test_automation_registry_keeps_python_without_approval():
    from agent_core.tools.python import register_python_tool
    from backend.services.automations.authority import scope_registry

    registry = ToolRegistry()
    register_python_tool(registry, object())
    scoped = scope_registry(registry)
    assert not scoped.get("run_python_code").policy.requires_approval


def test_application_registers_no_redundant_file_tools():
    from backend.dependencies_tools import tool_registry

    names = {item.tool.name for item in tool_registry.list_accessible_tools("user")}
    assert "run_python_code" in names
    assert not names & {
        "artifact_inspect",
        "artifact_create",
        "artifact_edit",
        "run_artifact_python",
        "artifact_rebuild_pdf_text_only",
    }
