"""Envelope validation only. Office content parsing runs in isolated containers."""

import stat
import struct
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from uuid import UUID, uuid4

from backend.services.artifacts.storage import ArtifactStorageError, StoredArtifact


def validate_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    if (
        Path(name).suffix.lower().lstrip(".")
        not in {"xlsx", "docx", "pdf", "txt", "md", "csv", "json", "png"}
        or not 1 < len(name) <= 255
        or any(c in name for c in "/\\:")
        or any(unicodedata.category(c).startswith("C") for c in name)
        or not Path(name).stem.strip(". ")
    ):
        raise ArtifactStorageError("Choose a valid XLSX, DOCX, PDF or TXT filename.", 415)
    return name


def validate_archive(path: Path, *, max_unpacked_bytes=104857600, max_entries=2000) -> None:
    """Inspect a bounded ZIP directory without extracting or parsing Office XML."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > max_entries:
                raise ArtifactStorageError("Invalid artifact archive entry count.")
            if sum(item.file_size for item in entries) > max_unpacked_bytes:
                raise ArtifactStorageError("Artifact archive exceeds unpacked size limit.")
            names = set()
            for item in entries:
                name = item.filename
                if (
                    name in names
                    or name.startswith("/")
                    or ".." in PurePosixPath(name).parts
                    or "\\" in name
                    or ":" in name
                    or "\x00" in name
                    or item.flag_bits & 1
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or (
                        item.file_size > 1024 * 1024
                        and item.file_size > 200 * max(item.compress_size, 1)
                    )
                ):
                    raise ArtifactStorageError("Unsafe artifact archive entry.")
                names.add(name)
    except (zipfile.BadZipFile, OSError, NotImplementedError) as error:
        raise ArtifactStorageError("Invalid .xlsx archive.") from error


def validate_preview_png(path: Path):
    with path.open("rb") as source:
        header = source.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ArtifactStorageError("Invalid PNG preview asset")
    width, height = struct.unpack("!II", header[16:24])
    if not 1 <= width <= 1600 or not 1 <= height <= 1600:
        raise ArtifactStorageError("PNG preview dimensions exceed limits")
    return width, height


@dataclass(frozen=True)
class ValidatedCompanion:
    role: Literal["editable_source", "preview_pdf", "preview_page"]
    stored: StoredArtifact
    asset_id: UUID = field(default_factory=uuid4)
    page: int | None = None

    def record(self, version_id):
        return {
            "role": self.role,
            "storage_key": self.stored.key,
            "size_bytes": self.stored.size_bytes,
            "sha256": self.stored.sha256,
            "mime_type": self.stored.mime_type,
            "asset_id": str(self.asset_id),
            "page": self.page,
            "version_id": str(version_id),
        }


@dataclass(frozen=True)
class ValidatedArtifact:
    stored: StoredArtifact
    name: str
    origin: str = "agent"
    calculation_status: str = "not_calculated"
    preview_manifest: dict[str, Any] = field(default_factory=dict)
    companion_sources: list[dict[str, Any]] = field(default_factory=list)
    source_document_id: UUID | None = None
    source_document_version_id: UUID | None = None
    source_run_id: UUID | None = None
    companions: tuple[ValidatedCompanion, ...] = ()
