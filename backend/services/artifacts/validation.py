"""Envelope validation only. Office content parsing runs in isolated containers."""

import stat
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from backend.services.artifacts.storage import ArtifactStorageError, StoredArtifact


def validate_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name)
    if (
        not name.lower().endswith(".xlsx")
        or not 5 < len(name) <= 255
        or any(c in name for c in "/\\:")
        or any(unicodedata.category(c).startswith("C") for c in name)
        or not Path(name).stem.strip(". ")
    ):
        raise ArtifactStorageError("Choose a valid .xlsx filename.", 415)
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
