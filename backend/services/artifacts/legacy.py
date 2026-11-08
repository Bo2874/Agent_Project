"""Run-local adapters for saved checkpoints; never loaded for a new task."""

from dataclasses import replace

from pydantic import BaseModel, ConfigDict, Field

from agent_core.tools import ToolExecutionPolicy, ToolResult, create_context_tool

OLD_FILE_TOOLS = frozenset(
    {
        "artifact_inspect",
        "artifact_create",
        "artifact_edit",
        "run_artifact_python",
        "artifact_rebuild_pdf_text_only",
    }
)


class LegacyPythonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32000)
    stdin: str | None = Field(default=None, max_length=65536)


def checkpoint_registry(registry, values):
    names = set(values.get("loaded_tool_names", [])) | {
        call.get("name") for call in values.get("pending_tool_calls", [])
    }
    definitions = values.get("tool_definitions", [])
    new_python = any(
        definition.get("function", {}).get("name") == "run_python_code"
        and "outputs" in definition.get("function", {}).get("parameters", {}).get("properties", {})
        for definition in definitions
    )
    old_python = "run_python_code" in names and not new_python
    if not names & OLD_FILE_TOOLS and not old_python:
        return registry
    result = registry.fork()
    if names & OLD_FILE_TOOLS:
        from agent_core.tools.artifacts import register_artifact_tools
        from backend.services.artifacts.tools import ArtifactToolService

        register_artifact_tools(result, ArtifactToolService())
        entry = result.get("run_artifact_python")
        result.replace_owned(
            [entry],
            [
                replace(
                    entry,
                    policy=replace(entry.policy, requires_approval=False),
                    capability=entry.capability.model_copy(
                        update={"requires_approval": False, "immutable_output": True}
                    ),
                )
            ],
        )
    if old_python:

        async def retired(context, arguments):
            return ToolResult.failure(
                "Runtime Python cũ đã ngừng dùng. Code của checkpoint này chưa được chạy lại. "
                "Hãy gửi yêu cầu mới để dùng runtime hợp nhất; file cũ vẫn được giữ nguyên.",
                error_code="legacy_python_runtime_retired",
            )

        if "run_python_code" in result:
            result.unregister("run_python_code")
        result.register(
            create_context_tool(
                name="run_python_code",
                description=(
                    "Saved legacy Python checkpoint; explains migration without executing code."
                ),
                args_schema=LegacyPythonInput,
                coroutine=retired,
            ),
            policy=ToolExecutionPolicy(requires_approval=False),
        )
    return result
