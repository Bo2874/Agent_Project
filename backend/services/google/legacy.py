"""Non-executing adapters only for saved Google checkpoints; never a new catalog."""

from pydantic import BaseModel, ConfigDict

from agent_core.tools import ToolExecutionPolicy, ToolResult, create_context_tool

RETIRED_GOOGLE_TOOLS = frozenset(
    {
        "google_drive_prepare",
        "google_drive_upload",
        "google_docs_read",
        "google_docs_prepare",
        "google_docs_create",
        "google_docs_edit",
        "google_sheets_read",
        "google_sheets_prepare",
        "google_sheets_append",
        "google_sheets_update",
    }
)


class SavedGoogleArguments(BaseModel):
    model_config = ConfigDict(extra="allow")


def checkpoint_registry(registry, values):
    names = set(values.get("loaded_tool_names", [])) | {
        call.get("name") for call in values.get("pending_tool_calls", [])
    }
    names &= RETIRED_GOOGLE_TOOLS
    if not names:
        return registry
    result = registry.fork()

    async def retired(context, arguments):
        return ToolResult.failure(
            "Tool Google cũ đã ngừng dùng. Thao tác chưa hoàn tất không được chạy lại. "
            "Hãy gửi yêu cầu mới để dùng run_python_code; kết quả và journal cũ vẫn được giữ.",
            error_code="legacy_google_tools_retired",
        )

    for name in sorted(names):
        if name not in result:
            result.register(
                create_context_tool(
                    name=name,
                    description="Retired saved Google call: no new execution or approval.",
                    args_schema=SavedGoogleArguments,
                    coroutine=retired,
                ),
                policy=ToolExecutionPolicy(requires_approval=False),
            )
    return result
