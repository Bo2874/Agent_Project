"""Bounded binary streams and opaque, create-only private storage keys."""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_TYPES = {
    "xlsx": XLSX_MIME,
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "png": "image/png",  # Internal preview assets, not an upload format.
}
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_KEY = re.compile(rf"{_UUID}/{_UUID}/{_UUID}/content\.(xlsx|docx|pdf|txt|png)")


class ArtifactStorageError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        self.detail, self.status_code = detail, status_code
        super().__init__(detail)


@dataclass(frozen=True)
class StoredArtifact:
    key: str
    size_bytes: int
    sha256: str
    mime_type: str = XLSX_MIME


class ArtifactStorage:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ArtifactStorageError("Artifact storage cannot be a symlink.")
        self.root = self.root.resolve()
        # Python on Windows needs the extended path prefix when long paths are
        # disabled in the OS. Keep opaque keys portable; adapt only the root.
        if os.name == "nt" and not str(self.root).startswith("\\\\?\\"):
            self.root = Path("\\\\?\\" + str(self.root))

    def path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ArtifactStorageError("Invalid artifact storage key.")
        target = self.root.joinpath(*key.split("/"))
        current = target
        while current != self.root:
            if current.is_symlink():
                raise ArtifactStorageError("Artifact symlinks are not supported.")
            current = current.parent
        if not target.resolve().is_relative_to(self.root):
            raise ArtifactStorageError("Artifact path escapes storage.")
        return target

    def put(self, source: BinaryIO, key: str, *, max_bytes: int) -> StoredArtifact:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.path(key)
        size = 0
        digest = hashlib.sha256()
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(target, flags, 0o600), "wb") as output:
                created = True
                while chunk := source.read(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ArtifactStorageError("Artifact exceeds file size limit.", 413)
                    output.write(chunk)
                    digest.update(chunk)
                if not size:
                    raise ArtifactStorageError("Empty artifact files are not supported.")
                output.flush()
                os.fsync(output.fileno())
        except (OSError, ValueError) as error:
            if created:
                target.unlink(missing_ok=True)
            if isinstance(error, ArtifactStorageError):
                raise
            raise ArtifactStorageError("Cannot store artifact safely.") from error
        return StoredArtifact(key, size, digest.hexdigest(), MIME_TYPES[target.suffix[1:]])

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def verify(self, stored: StoredArtifact, *, max_bytes: int) -> None:
        digest = hashlib.sha256()
        size = 0
        with self.path(stored.key).open("rb") as source:
            while chunk := source.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ArtifactStorageError("Artifact exceeds file size limit.", 413)
                digest.update(chunk)
        if size != stored.size_bytes or digest.hexdigest() != stored.sha256:
            raise ArtifactStorageError("Artifact checksum mismatch.")
