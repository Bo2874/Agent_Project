from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PublishedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: UUID
    version_id: UUID
    parent_version_id: UUID | None
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    calculation_status: Literal["not_calculated", "fresh", "unsupported", "failed"]
