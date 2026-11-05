import hashlib
import json
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from artifact_runtime.base import StrictModel
from artifact_runtime.document_types import (  # noqa: F401 - shared schema exports
    DocumentBlocks,
    DocumentEdits,
    DocumentOutline,
    PreviewManifest,
)
from artifact_runtime.pdf_types import PdfTextDocument, TextOnlyRebuild  # noqa: F401
from artifact_runtime.spreadsheet_types import SpreadsheetPlan

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MIME = "application/pdf"
PNG_MIME = "image/png"
GENERATED_TYPES = {".xlsx": XLSX_MIME, ".docx": DOCX_MIME, ".pdf": PDF_MIME, ".png": PNG_MIME}
MAX_FILE_BYTES = 20971520
MAX_TOTAL_BYTES = 41943040
MAX_MANIFEST_BYTES = 1048576


class PublishedArtifact(StrictModel):
    artifact_id: UUID
    version_id: UUID
    parent_version_id: UUID | None
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    calculation_status: Literal["not_calculated", "fresh", "unsupported", "failed"]


class ArtifactOperation(StrictModel):
    kind: Literal[
        "create_xlsx",
        "edit_xlsx",
        "create_docx",
        "edit_docx",
        "rebuild_pdf",
        "rebuild_pdf_text_only",
        "python",
    ]
    parent_version_id: UUID | None = None
    payload: SpreadsheetPlan | DocumentBlocks | DocumentEdits | TextOnlyRebuild | dict = Field(
        default_factory=dict, validate_default=True
    )
    code: str | None = Field(default=None, min_length=1, max_length=32000)

    @field_validator("payload", mode="before")
    @classmethod
    def typed_payload(cls, value, info):
        if info.data.get("kind") == "rebuild_pdf_text_only":
            return TextOnlyRebuild.model_validate(value)
        if info.data.get("kind") == "rebuild_pdf":
            if isinstance(value, DocumentEdits | DocumentBlocks):
                return value
            return (DocumentEdits if "operations" in value else DocumentBlocks).model_validate(
                value
            )
        schema = {
            "create_xlsx": SpreadsheetPlan,
            "edit_xlsx": SpreadsheetPlan,
            "create_docx": DocumentBlocks,
            "edit_docx": DocumentEdits,
        }.get(info.data.get("kind"))
        return schema.model_validate(value) if schema else value

    @field_serializer("payload")
    def wire_payload(self, value):
        # Old saved jobs omitted optional defaults. Preserve that exact wire shape.
        return (
            value.model_dump(mode="json", exclude_unset=True)
            if isinstance(value, BaseModel)
            else value
        )

    @model_validator(mode="after")
    def valid_kind(self):
        if self.kind == "python":
            if not self.code or self.payload:
                raise ValueError("Python operations require code and no structured payload")
        elif self.code is not None:
            raise ValueError("Structured operations cannot contain code")
        if self.kind.startswith("edit_") and self.parent_version_id is None:
            raise ValueError("Editing requires a parent version")
        if self.kind == "rebuild_pdf_text_only" and self.parent_version_id is None:
            raise ValueError("Text-only approval requires a pinned source PDF")
        if self.kind.startswith("create_") and self.parent_version_id is not None:
            raise ValueError("Creating cannot specify a parent version")
        return self


class BlobDescriptor(StrictModel):
    name: str = Field(pattern=r"^(input|output)-[0-2]\.(xlsx|docx|pdf|png)$")
    mime_type: str = XLSX_MIME
    size_bytes: int = Field(gt=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def matching_type(self):
        if GENERATED_TYPES[Path(self.name).suffix] != self.mime_type:
            raise ValueError("Filename and MIME type must match")
        return self

    @classmethod
    def from_bytes(cls, name: str, content: bytes):
        return cls(
            name=name,
            mime_type=GENERATED_TYPES[Path(name).suffix],
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )


class ArtifactRunRequest(StrictModel):
    job_id: UUID
    action: Literal["execute", "inspect", "recalculate"] = "execute"
    operation: ArtifactOperation | None = None
    inputs: tuple[BlobDescriptor, ...] = Field(default=(), max_length=3)
    preview_sheet: str | None = Field(default=None, max_length=31)
    preview_offset: int = Field(default=0, ge=0, le=1048575)
    preview_limit: int = Field(default=100, ge=1, le=100)
    preview_text_offset: int = Field(
        default=0, ge=0, le=200000, exclude_if=lambda value: value == 0
    )

    @model_validator(mode="after")
    def valid_request(self):
        if self.action == "execute" and self.operation is None:
            raise ValueError("Execution requires an operation")
        if self.action != "execute" and (self.operation is not None or len(self.inputs) != 1):
            raise ValueError("Inspection/recalculation requires exactly one input and no code")
        if sum(item.size_bytes for item in self.inputs) > MAX_TOTAL_BYTES:
            raise ValueError("Too many input bytes")
        if [Path(item.name).stem for item in self.inputs] != [
            f"input-{i}" for i in range(len(self.inputs))
        ]:
            raise ValueError("Input names must be generated in sequence")
        return self


class CalculationReport(StrictModel):
    status: Literal["not_calculated", "fresh", "unsupported", "failed"] = "not_calculated"
    warnings: tuple[str, ...] = Field(default=(), max_length=100)


class GeneratedArtifact(StrictModel):
    files: tuple[BlobDescriptor, ...] = Field(default=(), max_length=3)
    calculation: CalculationReport = Field(default_factory=CalculationReport)
    preview_manifest: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self):
        if sum(item.size_bytes for item in self.files) > MAX_TOTAL_BYTES:
            raise ValueError("Too many output bytes")
        if [Path(item.name).stem for item in self.files] != [
            f"output-{i}" for i in range(len(self.files))
        ]:
            raise ValueError("Output names must be generated in sequence")
        return self


@dataclass(frozen=True)
class ArtifactInput:
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    source: BinaryIO

    def descriptor(self) -> BlobDescriptor:
        return BlobDescriptor(
            name=self.name, mime_type=self.mime_type, size_bytes=self.size_bytes, sha256=self.sha256
        )


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def bundle_header(manifest: dict) -> bytes:
    body = canonical(manifest)
    if len(body) > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest exceeds limit")
    return struct.pack("!I", len(body)) + body


def read_exact(source: BinaryIO, count: int) -> bytes:
    data = source.read(count)
    if len(data) != count:
        raise ValueError("Truncated artifact bundle")
    return data


def read_manifest(source: BinaryIO) -> dict:
    size = struct.unpack("!I", read_exact(source, 4))[0]
    if not 0 < size <= MAX_MANIFEST_BYTES:
        raise ValueError("Invalid artifact manifest size")
    return json.loads(read_exact(source, size))


def copy_checked(source: BinaryIO, output: BinaryIO, descriptor: BlobDescriptor) -> None:
    digest = hashlib.sha256()
    remaining = descriptor.size_bytes
    while remaining:
        chunk = source.read(min(65536, remaining))
        if not chunk:
            raise ValueError("Truncated artifact blob")
        digest.update(chunk)
        output.write(chunk)
        remaining -= len(chunk)
    if digest.hexdigest() != descriptor.sha256:
        raise ValueError("Artifact blob checksum mismatch")


def collect_outputs(
    root: Path, *, max_file_bytes=MAX_FILE_BYTES
) -> tuple[tuple[BlobDescriptor, Path], ...]:
    if root.is_symlink():
        raise ValueError("Output directory cannot be a symlink")
    paths = sorted(root.iterdir())
    if not 1 <= len(paths) <= 3:
        raise ValueError("Expected between one and three output files")
    result = []
    total = 0
    for index, path in enumerate(paths):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.suffix not in GENERATED_TYPES:
            raise ValueError("Only regular supported output files are allowed")
        if not 0 < info.st_size <= max_file_bytes:
            raise ValueError("Output file exceeds size limit")
        total += info.st_size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Output files exceed total size limit")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        result.append(
            (
                BlobDescriptor(
                    name=f"output-{index}{path.suffix}",
                    mime_type=GENERATED_TYPES[path.suffix],
                    size_bytes=info.st_size,
                    sha256=digest.hexdigest(),
                ),
                path,
            )
        )
    return tuple(result)


def write_bundle(output: BinaryIO, manifest: GeneratedArtifact, paths: tuple[Path, ...]) -> None:
    if len(paths) != len(manifest.files):
        raise ValueError("Output count mismatch")
    output.write(bundle_header(manifest.model_dump(mode="json")))
    for descriptor, path in zip(manifest.files, paths, strict=True):
        with path.open("rb") as source:
            copy_checked(source, output, descriptor)
    output.flush()


def unpack_bundle(source: BinaryIO, root: Path) -> tuple[GeneratedArtifact, tuple[Path, ...]]:
    manifest = GeneratedArtifact.model_validate(read_manifest(source))
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        for descriptor in manifest.files:
            path = root / descriptor.name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags, 0o600), "wb") as target:
                paths.append(path)
                copy_checked(source, target, descriptor)
        if source.read(1):
            raise ValueError("Unexpected trailing bundle content")
        return manifest, tuple(paths)
    except BaseException:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
