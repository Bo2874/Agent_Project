from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


class GoogleFileRef(GoogleModel):
    connection_id: UUID
    file_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    context_id: UUID
