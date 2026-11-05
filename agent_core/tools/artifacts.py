"""File tools accept structured data; credentials and input paths belong to the host."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_core.contracts import CapabilityDescriptor
from agent_core.tools import (
    ToolExecutionPolicy,
    ToolRegistry,
    ToolRoutingPolicy,
    create_context_tool,
)
from artifact_runtime.document_types import DocumentBlocks, DocumentEdits
from artifact_runtime.pdf_types import TextOnlyRebuild
from artifact_runtime.protocol import ArtifactOperation
from artifact_runtime.spreadsheet_types import SpreadsheetPlan


class CreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=255)
    format: Literal["xlsx", "docx", "pdf"] = "xlsx"
    payload: SpreadsheetPlan | DocumentBlocks

    @model_validator(mode="after")
    def matching_payload(self):
        if self.format == "pdf" and isinstance(self.payload, DocumentBlocks | DocumentEdits):
            if not isinstance(self, EditInput) and not isinstance(self.payload, DocumentBlocks):
                raise ValueError("Creating a PDF requires content blocks")
            return self
        expected = (
            SpreadsheetPlan
            if self.format == "xlsx"
            else (DocumentEdits if isinstance(self, EditInput) else DocumentBlocks)
        )
        if not isinstance(self.payload, expected):
            raise ValueError("Payload does not match the requested file format")
        return self


class EditInput(CreateInput):
    name: str | None = Field(default=None, max_length=255)
    parent_version_id: UUID
    payload: SpreadsheetPlan | DocumentBlocks | DocumentEdits


class InspectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID
    sheet: str | None = Field(default=None, max_length=31)
    offset: int = Field(default=0, ge=0, le=1048575)
    limit: int = Field(default=20, ge=1, le=100)
    text_offset: int = Field(default=0, ge=0, le=200000)


class TextOnlyPdfInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=255)
    parent_version_id: UUID
    payload: TextOnlyRebuild


class PythonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="report.xlsx", max_length=255)
    parent_version_id: UUID | None = None
    code: str = Field(min_length=1, max_length=32000)


def register_artifact_tools(registry: ToolRegistry, service) -> None:
    descriptions = {
        "artifact_inspect": (
            "Read a selected or chat-created Excel, Word or text PDF: sheets/cells or stable "
            "document node IDs, headings, paragraphs and tables. Inspect Word before edits. "
            "For uploaded PDFs, reports text coverage and unreadable pages. Offset pages/blocks. "
            "For long PDF pages use limit=1, text_offset=next_text_offset until all text is read. "
            "IDs come from trusted selected_artifacts metadata or artifact_refs."
        ),
        "artifact_create": (
            "Create a downloadable file. Set format=xlsx for operations: write_cells, add_sheet, "
            "set_formula (SUM/SUMIF/AVERAGE/COUNT/IF), format_range, add_chart (column/bar/line). "
            "Or format=docx with payload.blocks: heading(text,level), paragraph(text), "
            "list(text,ordered), table(rows). Or format=pdf with these blocks; retains an editable "
            "DOCX source. Plain text, not HTML. Returns an immutable version."
        ),
        "artifact_edit": (
            "Edit a selected or conversation-created Excel or Word version. Inspect first. Supply "
            "parent_version_id and typed operations; preserves original and returns a new version. "
            "DOCX edits: replace_text(node_id,old,new), format_block(node_id,style,font_size), "
            "set_table_cell(node_id,row,column,text), append_blocks(blocks). IDs must be from the "
            "exact parent version. For format=pdf, use these edits when inspect returns node IDs, "
            "otherwise supply complete payload.blocks to rebuild/reflow all desired text. Original "
            "page layout may change. Partial PDFs require artifact_rebuild_pdf_text_only approval. "
            "Images, fields, footnotes and revisions are refused."
        ),
        "run_artifact_python": (
            "For complex Excel/Word work beyond structured tools, run Python in an isolated "
            "offline sandbox, after user approval. Read optional parent at /input/input-0.xlsx; "
            "or /input/input-0.docx; write /output/output-0.xlsx or output-0.docx, matching "
            "the output name. openpyxl and python-docx available. No credentials, network or "
            "host files. Macro/image/external-link workbooks are refused. "
            "Prefer artifact_create/artifact_edit."
        ),
        "artifact_rebuild_pdf_text_only": (
            "ONLY after explaining incomplete PDF coverage: rebuild a pinned PDF from chosen text "
            "blocks, omitting images/unreadable content. Requires user approval of these exact "
            "arguments. payload.content.blocks contains the full desired document; "
            "payload.omitted_pages must exactly match artifact_inspect coverage. No OCR. "
            "Returns a new PDF and keeps the original unchanged."
        ),
    }

    async def inspect(context, args):
        return await service.inspect(context, **InspectInput.model_validate(args).model_dump())

    def handler(kind, schema):
        async def execute(context, args):
            data = schema.model_validate(args).model_dump(mode="json")
            name = data.pop("name")
            format = data.pop("format", None)
            operation_kind = (
                kind
                if kind in {"python", "rebuild_pdf_text_only"}
                else ("rebuild_pdf" if format == "pdf" else kind.split("_")[0] + "_" + format)
            )
            if kind.startswith("create_") and name is None:
                name = "report." + format
            return await service.execute(
                context, ArtifactOperation(kind=operation_kind, **data), name=name
            )

        return execute

    entries = (
        ("artifact_inspect", InspectInput, inspect),
        ("artifact_create", CreateInput, handler("create_xlsx", CreateInput)),
        ("artifact_edit", EditInput, handler("edit_xlsx", EditInput)),
        ("run_artifact_python", PythonInput, handler("python", PythonInput)),
        (
            "artifact_rebuild_pdf_text_only",
            TextOnlyPdfInput,
            handler("rebuild_pdf_text_only", TextOnlyPdfInput),
        ),
    )
    for name, schema, coroutine in entries:
        approval = name in {"run_artifact_python", "artifact_rebuild_pdf_text_only"}
        registry.register(
            create_context_tool(
                name=name, description=descriptions[name], args_schema=schema, coroutine=coroutine
            ),
            policy=ToolExecutionPolicy(timeout_seconds=300, requires_approval=approval),
            routing=ToolRoutingPolicy(
                keywords=frozenset(
                    {
                        "excel",
                        "xlsx",
                        "bảng tính",
                        "file",
                        "spreadsheet",
                        "word",
                        "docx",
                        "pdf",
                        "tài liệu",
                    }
                )
            ),
            capability=CapabilityDescriptor(
                capability_id=name,
                kind="tool",
                name=name,
                description=descriptions[name],
                version="2.0.0",
                side_effect="read_only" if name == "artifact_inspect" else "write",
                requires_approval=approval,
                immutable_output=name in {"artifact_create", "artifact_edit"},
            ),
        )
