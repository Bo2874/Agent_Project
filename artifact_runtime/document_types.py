"""Bounded document commands. No Office parsing takes place in this module."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from artifact_runtime.base import StrictModel

Text = Annotated[str, Field(max_length=20000)]
NodeId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}:[0-9]{1,5}$")]


class TextBlock(StrictModel):
    kind: Literal["heading", "paragraph", "list"]
    text: Text
    level: int = Field(default=1, ge=1, le=9)
    ordered: bool = False


class TableBlock(StrictModel):
    kind: Literal["table"]
    rows: list[list[Text]] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def rectangular(self):
        width = len(self.rows[0])
        if not 1 <= width <= 30 or any(len(row) != width for row in self.rows):
            raise ValueError("Table must be rectangular with 1-30 columns")
        return self


Block = Annotated[TextBlock | TableBlock, Field(discriminator="kind")]


class DocumentBlocks(StrictModel):
    blocks: list[Block] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def bounded_text(self):
        if len(self.model_dump_json()) > 400000:
            raise ValueError("Document content exceeds text limit")
        return self


class ReplaceText(StrictModel):
    kind: Literal["replace_text"]
    node_id: NodeId
    old: Annotated[str, Field(min_length=1, max_length=20000)]
    new: Text


class FormatBlock(StrictModel):
    kind: Literal["format_block"]
    node_id: NodeId
    style: str | None = Field(default=None, max_length=80)
    font_size: float | None = Field(default=None, ge=6, le=72)
    bold: bool | None = None
    italic: bool | None = None
    alignment: Literal["left", "center", "right", "justify"] | None = None


class SetTableCell(StrictModel):
    kind: Literal["set_table_cell"]
    node_id: NodeId
    row: int = Field(ge=0, le=9999)
    column: int = Field(ge=0, le=99)
    text: Text


class AppendBlocks(DocumentBlocks):
    kind: Literal["append_blocks"]


Edit = Annotated[
    ReplaceText | FormatBlock | SetTableCell | AppendBlocks, Field(discriminator="kind")
]


class DocumentEdits(StrictModel):
    operations: list[Edit] = Field(min_length=1, max_length=200)


class DocumentNode(StrictModel):
    node_id: NodeId
    kind: Literal["heading", "paragraph", "list", "table"]
    text: Text = ""
    style: str | None = None
    rows: list[list[Text]] = Field(default_factory=list)


class DocumentOutline(StrictModel):
    source_sha256: str
    nodes: list[DocumentNode]
    total_nodes: int
    warnings: list[str] = Field(default_factory=list)


class PreviewManifest(StrictModel):
    kind: Literal["pages"] = "pages"
    page_count: int = Field(ge=1, le=20)
    assets: list[dict] = Field(default_factory=list, max_length=20)
    source_sha256: str
