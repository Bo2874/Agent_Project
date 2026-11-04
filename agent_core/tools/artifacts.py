"""File tools accept structured data; credentials and input paths belong to the host."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agent_core.contracts import CapabilityDescriptor
from agent_core.tools import (
    ToolExecutionPolicy,
    ToolRegistry,
    ToolRoutingPolicy,
    create_context_tool,
)
from artifact_runtime.protocol import ArtifactOperation
from artifact_runtime.spreadsheet_types import SpreadsheetPlan


class CreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="report.xlsx", max_length=255)
    payload: SpreadsheetPlan


class EditInput(CreateInput):
    name: str | None = Field(default=None, max_length=255)
    parent_version_id: UUID


class InspectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID
    sheet: str | None = Field(default=None, max_length=31)
    offset: int = Field(default=0, ge=0, le=1048575)


class PythonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="report.xlsx", max_length=255)
    parent_version_id: UUID | None = None
    code: str = Field(min_length=1, max_length=32000)


def register_artifact_tools(registry: ToolRegistry, service) -> None:
    descriptions = {
        "artifact_inspect": (
            "Read a selected or conversation-created Excel version: sheets, cells, formulas and "
            "cached values. Use instead of RAG for working spreadsheets; page with offset/sheet. "
            "IDs come from trusted selected_artifacts metadata or artifact_refs."
        ),
        "artifact_create": (
            "Create a downloadable Excel .xlsx using typed operations: write_cells, add_sheet, "
            "set_formula (SUM/SUMIF/AVERAGE/COUNT/IF), format_range, add_chart (column/bar/line). "
            "Returns a new immutable version. Use for requests to create actual files."
        ),
        "artifact_edit": (
            "Edit a selected or conversation-created Excel version. Inspect first. Supply "
            "parent_version_id and typed operations; preserves original and returns a new version. "
            "This is a file action, not document RAG QA."
        ),
        "run_artifact_python": (
            "For complex Excel work unsupported by structured tools, run Python in an isolated "
            "offline sandbox, after user approval. Read optional parent at /input/input-0.xlsx; "
            "write exactly /output/output-0.xlsx. openpyxl available. No credentials, network or "
            "host files. Macro/image/external-link workbooks are refused. "
            "Prefer artifact_create/artifact_edit."
        ),
    }

    async def inspect(context, args):
        return await service.inspect(context, **InspectInput.model_validate(args).model_dump())

    def handler(kind, schema):
        async def execute(context, args):
            data = schema.model_validate(args).model_dump(mode="json")
            name = data.pop("name")
            return await service.execute(context, ArtifactOperation(kind=kind, **data), name=name)

        return execute

    entries = (
        ("artifact_inspect", InspectInput, inspect),
        ("artifact_create", CreateInput, handler("create_xlsx", CreateInput)),
        ("artifact_edit", EditInput, handler("edit_xlsx", EditInput)),
        ("run_artifact_python", PythonInput, handler("python", PythonInput)),
    )
    for name, schema, coroutine in entries:
        approval = name == "run_artifact_python"
        registry.register(
            create_context_tool(
                name=name, description=descriptions[name], args_schema=schema, coroutine=coroutine
            ),
            policy=ToolExecutionPolicy(timeout_seconds=300, requires_approval=approval),
            routing=ToolRoutingPolicy(
                keywords=frozenset({"excel", "xlsx", "bảng tính", "file", "spreadsheet"})
            ),
            capability=CapabilityDescriptor(
                capability_id=name,
                kind="tool",
                name=name,
                description=descriptions[name],
                version="1.0.0",
                side_effect="read_only" if name == "artifact_inspect" else "write",
                requires_approval=approval,
                immutable_output=name in {"artifact_create", "artifact_edit"},
            ),
        )
