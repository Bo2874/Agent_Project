from typing import Literal

from pydantic import Field

from artifact_runtime.base import StrictModel
from artifact_runtime.document_types import DocumentBlocks


class TextOnlyRebuild(StrictModel):
    content: DocumentBlocks
    omitted_pages: list[int] = Field(min_length=1, max_length=20)


class PdfPage(StrictModel):
    number: int
    text: str = Field(max_length=200000)
    has_images: bool = False


class PdfTextDocument(StrictModel):
    source_sha256: str
    pages: list[PdfPage] = Field(max_length=20)
    coverage: Literal["complete", "partial", "unsupported_scanned_pdf"]
    omitted_pages: list[int] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=30)
