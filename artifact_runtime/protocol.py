import hashlib
import json
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_FILE_BYTES = 20971520
MAX_TOTAL_BYTES = 41943040
MAX_MANIFEST_BYTES = 1048576


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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
    kind: Literal["create_xlsx", "edit_xlsx", "python"]
    parent_version_id: UUID | None = None
    payload: dict = Field(default_factory=dict)
    code: str | None = Field(default=None, min_length=1, max_length=32000)

    @model_validator(mode="after")
    def valid_kind(self):
        if self.kind == "python":
            if not self.code or self.payload:
                raise ValueError("Python operations require code and no structured payload")
        elif self.code is not None:
            raise ValueError("Structured operations cannot contain code")
        if self.kind == "edit_xlsx" and self.parent_version_id is None:
            raise ValueError("Editing requires a parent version")
        if self.kind == "create_xlsx" and self.parent_version_id is not None:
            raise ValueError("Creating cannot specify a parent version")
        return self


class BlobDescriptor(StrictModel):
    name: str = Field(pattern=r"^(input|output)-[0-2]\.xlsx$")
    mime_type: Literal["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"] = (
        XLSX_MIME
    )
    size_bytes: int = Field(gt=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_bytes(cls, name: str, content: bytes):
        return cls(name=name, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


class ArtifactRunRequest(StrictModel):
    job_id: UUID
    action: Literal["execute", "inspect", "recalculate"] = "execute"
    operation: ArtifactOperation | None = None
    inputs: tuple[BlobDescriptor, ...] = Field(default=(), max_length=3)
    preview_sheet: str | None = Field(default=None, max_length=31)
    preview_offset: int = Field(default=0, ge=0, le=1048575)
    preview_limit: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def valid_request(self):
        if self.action == "execute" and self.operation is None:
            raise ValueError("Execution requires an operation")
        if self.action != "execute" and (self.operation is not None or len(self.inputs) != 1):
            raise ValueError("Inspection/recalculation requires exactly one input and no code")
        if sum(item.size_bytes for item in self.inputs) > MAX_TOTAL_BYTES:
            raise ValueError("Too many input bytes")
        if [item.name for item in self.inputs] != [
            f"input-{i}.xlsx" for i in range(len(self.inputs))
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
        if [item.name for item in self.files] != [
            f"output-{i}.xlsx" for i in range(len(self.files))
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
        if not stat.S_ISREG(info.st_mode) or path.suffix.lower() != ".xlsx":
            raise ValueError("Only regular .xlsx output files are allowed")
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
                    name=f"output-{index}.xlsx", size_bytes=info.st_size, sha256=digest.hexdigest()
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
