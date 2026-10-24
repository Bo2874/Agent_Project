"""Extracted text coordinates refer to parsed.json, never to normalized embedding text."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreparationError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(message)


class ParsedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "heading", "table", "furniture"] = "text"
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    section: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    source_ref: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    table_header_rows: int = Field(default=0, ge=0)


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    original_text: str
    blocks: list[ParsedBlock]
    parser_version: str
    page_count: int | None = Field(default=None, ge=1)
    warnings: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_offsets(self):
        last = 0
        for block in self.blocks:
            if (
                block.start < last
                or block.end <= block.start
                or block.end > len(self.original_text)
            ):
                raise ValueError("Invalid extracted-text coordinates")
            if (block.page_start is None) != (block.page_end is None):
                raise ValueError("Page coordinates must be paired")
            if block.page_start is not None and block.page_end < block.page_start:
                raise ValueError("Invalid page range")
            last = block.end
        return self


class PreparedChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    embedding_text: str
    lexical_text: str
    token_count: int = Field(gt=0, le=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self):
        if not self.text.strip() or len(self.text) > 50_000 or self.end_offset <= self.start_offset:
            raise ValueError("Invalid source passage")
        if (self.page_start is None) != (self.page_end is None):
            raise ValueError("Page coordinates must be paired")
        if self.page_start is not None and (self.page_start < 1 or self.page_end < self.page_start):
            raise ValueError("Invalid page range")
        return self


class PreparedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    chunks: list[PreparedChunk]
    chunker_version: str
    tokenizer_revision: str
    warnings: list[dict[str, Any]] = Field(default_factory=list)
