from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from agent_core.contracts.base import (
    ContractModel,
    Identifier,
    PositiveInt,
    ShortText,
    Version,
)


class EvidenceReference(ContractModel):
    """Opaque, version-specific citation identity; ownership is checked separately."""

    citation_id: Identifier
    document_id: UUID
    version_id: UUID
    chunk_id: UUID


class RetrievalResult(EvidenceReference):
    """One retrieved passage with original text and source coordinates."""

    title: ShortText
    text: Annotated[StrictStr, Field(min_length=1, max_length=50_000)]
    score: Annotated[float, Field(strict=True)]
    score_kind: Literal["similarity", "distance", "lexical", "fusion", "rerank", "coverage"]
    retrieval_config_version: Version
    page_start: PositiveInt | None = None
    page_end: PositiveInt | None = None
    section: ShortText | None = None
    start_offset: Annotated[StrictInt, Field(ge=0)] | None = None
    end_offset: Annotated[StrictInt, Field(ge=0)] | None = None
    trust: Literal["untrusted"] = "untrusted"

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Retrieved text must not be blank")
        return value  # Preserve original whitespace for quotes/offsets.

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("page_start and page_end must be supplied together")
        if self.page_start is not None and self.page_end < self.page_start:
            raise ValueError("page_end must not precede page_start")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("start_offset and end_offset must be supplied together")
        if self.start_offset is not None and self.end_offset <= self.start_offset:
            raise ValueError("Offsets must describe a non-empty half-open range")
        if self.page_start is None and self.section is None and self.start_offset is None:
            raise ValueError("Evidence needs page, section or offset provenance")
        return self

    def reference(self) -> EvidenceReference:
        return EvidenceReference(
            citation_id=self.citation_id,
            document_id=self.document_id,
            version_id=self.version_id,
            chunk_id=self.chunk_id,
        )
