from uuid import uuid4

from agent_core.tools import ToolContext, ToolExecutor, ToolRegistry


async def test_old_python_checkpoint_is_not_reinterpreted_or_waiting_for_approval():
    from backend.services.artifacts.legacy import checkpoint_registry

    registry = ToolRegistry()
    values = {
        "loaded_tool_names": ["run_python_code"],
        "tool_definitions": [
            {"function": {"name": "run_python_code", "parameters": {"properties": {"code": {}}}}}
        ],
    }
    legacy = checkpoint_registry(registry, values)
    context = ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user")
    executor = ToolExecutor()
    assert not executor.needs_approval(legacy, context, "run_python_code", {"code": "print(1)"})
    result = await executor.execute(legacy, context, "run_python_code", {"code": "print(1)"})
    assert result.error_code == "legacy_python_runtime_retired"
    assert "run_python_code" not in registry


def test_new_checkpoint_never_loads_old_file_tools():
    from agent_core.tools.python import register_python_tool
    from backend.services.artifacts.legacy import checkpoint_registry

    registry = ToolRegistry()
    register_python_tool(registry, object())
    values = {
        "loaded_tool_names": ["run_python_code"],
        "tool_definitions": [registry.model_definition("run_python_code")],
    }
    assert checkpoint_registry(registry, values) is registry
