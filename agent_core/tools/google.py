"""Closed Google operations; credentials and authorization always belong to the host."""

from pydantic import Field

from agent_core.contracts import CapabilityDescriptor
from agent_core.tools import ToolExecutionPolicy, ToolRoutingPolicy, create_context_tool
from backend.schemas.google import GoogleCommitInput, GoogleFileRef, GoogleModel, SheetChange


class SheetReadInput(GoogleModel):
    ref: GoogleFileRef
    range_a1: str = Field(min_length=1, max_length=160)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=100)


class SheetPrepareInput(GoogleModel):
    ref: GoogleFileRef
    change: SheetChange


def register_google_tools(registry, service):
    entries = [
        (
            "google_sheets_read",
            SheetReadInput,
            "Read a selected native Google Sheet. Explicit A1 range (1000 rows/50 columns); "
            "pages up to 100 rows. Use selected_google_files refs; xlsx is not a native Sheet.",
        ),
        (
            "google_sheets_prepare",
            SheetPrepareInput,
            "Prepare a LOCAL immutable Sheets write preview. No remote write. Row writes "
            "use RAW text; set_formulas is explicit. format_range supports controlled styles. "
            "dedup_column is zero-based within the selected range. Returns exact commit_arguments.",
        ),
        (
            "google_sheets_append",
            GoogleCommitInput,
            "Commit append_rows using EXACT commit_arguments from google_sheets_prepare after "
            "approval. Never change preview. Unknown outcome requires reconciliation, never retry.",
        ),
        (
            "google_sheets_update",
            GoogleCommitInput,
            "Commit update_range/set_formulas/format_range from google_sheets_prepare using EXACT "
            "commit_arguments after approval. Changed snapshot requires a new proposal/approval.",
        ),
    ]
    for name, schema, description in entries:
        register_google_entry(registry, service, name, schema, description)


def register_google_entry(registry, service, name, schema, description):
    prepare = name.endswith("_prepare")
    read = name.endswith("_read")
    approval = not (prepare or read)

    async def invoke(context, arguments):
        return await service.invoke(context, name, arguments)

    registry.register(
        create_context_tool(
            name=name, description=description, args_schema=schema, coroutine=invoke
        ),
        policy=ToolExecutionPolicy(timeout_seconds=120, requires_approval=approval),
        routing=ToolRoutingPolicy(
            keywords=frozenset({"google", "sheets", "docs", "drive", "bảng tính", "tài liệu"})
        ),
        capability=CapabilityDescriptor(
            capability_id=name,
            kind="tool",
            name=name,
            description=description,
            version="1.0.0",
            side_effect="read_only" if read else "write",
            requires_approval=approval,
            immutable_output=prepare,
        ),
    )
