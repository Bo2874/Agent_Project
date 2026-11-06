from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
DOC_MIME = "application/vnd.google-apps.document"


class GoogleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class OAuthStart(GoogleModel):
    authorization_url: str
    expires_at: datetime


class ConnectionView(GoogleModel):
    id: UUID
    status: Literal["connected", "disconnected", "reconnect"]
    account_email: str | None
    account_name: str | None
    granted_scopes: list[str]
    offline_ready: bool


GoogleFileId = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")]


class GoogleFileRef(GoogleModel):
    connection_id: UUID
    file_id: GoogleFileId
    context_id: UUID


class GoogleFileSelection(GoogleModel):
    file_ids: list[GoogleFileId] = Field(max_length=100)


class GoogleWorkSelection(GoogleModel):
    file_refs: list[GoogleFileRef] = Field(max_length=20)


GoogleCell = StrictStr | StrictInt | StrictFloat | StrictBool | None


class SheetFormat(GoogleModel):
    bold: bool | None = None
    italic: bool | None = None
    background: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    number_format: Literal["TEXT", "NUMBER", "PERCENT", "CURRENCY", "DATE"] | None = None


class SheetChange(GoogleModel):
    kind: Literal["append_rows", "update_range", "set_formulas", "format_range"]
    range_a1: str = Field(min_length=1, max_length=160)
    rows: list[list[GoogleCell]] = Field(default_factory=list, max_length=100)
    format: SheetFormat | None = None
    dedup_column: int | None = Field(default=None, ge=0, le=49)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json()) > 200000:
            raise ValueError("Sheet write payload exceeds limit")
        if self.kind == "format_range":
            if self.format is None or self.rows or self.dedup_column is not None:
                raise ValueError("Formatting requires only an explicit format")
        elif (
            not self.rows
            or not 1 <= len(self.rows[0]) <= 50
            or any(len(row) != len(self.rows[0]) for row in self.rows)
            or self.format is not None
        ):
            raise ValueError("Write a rectangular matrix of at most 100 rows and 50 columns")
        if self.kind == "set_formulas" and any(
            not isinstance(cell, str) or not cell.startswith("=")
            for row in self.rows
            for cell in row
        ):
            raise ValueError("Formula writes require explicit formulas")
        if self.dedup_column is not None and (
            self.kind != "append_rows" or any(self.dedup_column >= len(row) for row in self.rows)
        ):
            raise ValueError("Dedup column must be present in appended rows")
        return self


class SheetSnapshot(GoogleModel):
    file_id: GoogleFileId
    range_a1: str
    values: list[list[GoogleCell]]
    snapshot_hash: str
    next_offset: int | None = None
    warnings: list[str] = Field(default_factory=list)


class GoogleWritePreview(GoogleModel):
    operation: str = Field(max_length=40)
    title: str = Field(max_length=600)
    target: str = Field(max_length=500)
    before: str = Field(max_length=12000)
    after: str = Field(max_length=12000)
    warning: str = Field(max_length=2000)


class GoogleCommitInput(GoogleModel):
    proposal_id: UUID
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview: GoogleWritePreview


class GoogleWriteProposal(GoogleModel):
    id: UUID
    digest: str
    snapshot_hash: str
    expires_at: datetime
    preview: GoogleWritePreview

    def commit_arguments(self):
        return {
            "proposal_id": str(self.id),
            "digest": self.digest,
            "preview": self.preview.model_dump(mode="json"),
        }


class GoogleWriteReceipt(GoogleModel):
    operation_id: UUID
    status: Literal["completed"] = "completed"
    updated_range: str = ""
    updated_rows: int = Field(default=0, ge=0)
    updated_cells: int = Field(default=0, ge=0)
    url: str
    file_ref: GoogleFileRef | None = None


class GoogleTextBlock(GoogleModel):
    tab_id: str | None
    start_index: int
    end_index: int
    text: str


class GoogleDocumentSnapshot(GoogleModel):
    file_id: GoogleFileId
    revision_id: str | None
    blocks: list[GoogleTextBlock]
    next_offset: int | None = None
    warnings: list[str] = Field(default_factory=list)


class DocumentTextOperation(GoogleModel):
    kind: Literal["insert_text", "delete_text", "replace_text"]
    start_index: int = Field(ge=1, le=2000000)
    end_index: int | None = Field(default=None, ge=2, le=2000000)
    text: str = Field(default="", max_length=50000)

    @model_validator(mode="after")
    def valid_operation(self):
        if self.kind == "insert_text":
            if not self.text or self.end_index is not None:
                raise ValueError("Insert requires text and no end index")
        elif self.end_index is None or self.end_index <= self.start_index:
            raise ValueError("Delete/replace requires a nonempty explicit range")
        if self.kind == "delete_text" and self.text:
            raise ValueError("Delete does not accept replacement text")
        self.text.encode("utf-16-le")
        if any(ord(char) < 32 and char not in "\n\t" for char in self.text):
            raise ValueError("Control characters are not supported")
        return self


class DocumentEdit(GoogleModel):
    revision_id: str = Field(min_length=1, max_length=256)
    tab_id: str | None = Field(default=None, min_length=1, max_length=128)
    operations: list[DocumentTextOperation] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def bounded_edit(self):
        if len(self.model_dump_json()) > 200000:
            raise ValueError("Document edit exceeds limit")
        return self
