from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
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


class GoogleWritePreview(GoogleModel):
    operation: str = Field(max_length=40)
    title: str = Field(max_length=600)
    target: str = Field(max_length=500)
    before: str = Field(max_length=12000)
    after: str = Field(max_length=12000)
    warning: str = Field(max_length=2000)


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


class GoogleFileAuthority(GoogleModel):
    grant_generation: int = Field(ge=1)
    connection_generation: int = Field(ge=1)
    mime_type: str


class GoogleWriteReceipt(GoogleModel):
    operation_id: UUID
    status: Literal["completed"] = "completed"
    updated_range: str = ""
    updated_rows: int = Field(default=0, ge=0)
    updated_cells: int = Field(default=0, ge=0)
    url: str
    file_ref: GoogleFileRef | None = None
    file_authority: GoogleFileAuthority | None = None
