from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from artifact_runtime.protocol import ArtifactOperation, PublishedArtifact


class ArtifactSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_ids: list[UUID] = Field(max_length=20)


class DocumentImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: UUID


__all__ = ["ArtifactOperation", "PublishedArtifact"]
