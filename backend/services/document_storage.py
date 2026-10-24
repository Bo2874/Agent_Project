"""Private local document storage and bounded upload-container validation."""

import asyncio
import hashlib
import os
import re
import shutil
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol
from uuid import UUID

import olefile
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MIME_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}
_GENERIC_MIMES = {"", "application/octet-stream", "binary/octet-stream"}
_KEY = re.compile(
    r"[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f-]{36}/(original\.(pdf|doc|docx|txt)|parsed\.json)"
)


class DocumentStorageError(ValueError):
    def __init__(self, detail: str, *, code="invalid_document", status_code=422):
        self.detail, self.code, self.status_code = detail, code, status_code
        super().__init__(detail)


@dataclass(frozen=True)
class StoredUpload:
    key: str
    size: int
    sha256: str
    warnings: list[dict[str, str]]


class DocumentStorage(Protocol):
    async def save(self, source: BinaryIO, key: str, max_bytes: int) -> StoredUpload: ...

    def download_path(self, key: str) -> Path: ...

    async def delete(self, key: str) -> None: ...


def validate_filename(filename: str | None, content_type: str | None) -> tuple[str, str]:
    name = unicodedata.normalize("NFC", (filename or "").replace("\\", "/").rsplit("/", 1)[-1])
    if (
        not name.strip()
        or len(name) > 255
        or any(unicodedata.category(char).startswith("C") for char in name)
    ):
        raise DocumentStorageError("Tên file không hợp lệ.")
    extension = Path(name).suffix.lower().lstrip(".")
    if extension not in MIME_TYPES or not Path(name).stem.strip(". "):
        raise DocumentStorageError(
            "Chỉ hỗ trợ PDF, Word DOC/DOCX và TXT.", status_code=415, code="unsupported_document"
        )
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime not in _GENERIC_MIMES | {MIME_TYPES[extension]}:
        raise DocumentStorageError(
            "Loại nội dung không khớp phần mở rộng của file.", code="document_mime_mismatch"
        )
    return name, extension


def original_key(owner: UUID, document: UUID, version: UUID, file_type: str) -> str:
    if file_type not in MIME_TYPES:
        raise DocumentStorageError("Định dạng file không hợp lệ.")
    return f"{owner}/{document}/{version}/original.{file_type}"


def parsed_key(owner: UUID, document: UUID, version: UUID) -> str:
    return f"{owner}/{document}/{version}/parsed.json"


def _validate_docx(path: Path, max_unpacked_bytes: int, max_entries: int) -> None:
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if (
            len(members) > max_entries
            or sum(item.file_size for item in members) > max_unpacked_bytes
        ):
            raise DocumentStorageError("DOCX vượt giới hạn nội dung sau giải nén.")
        names = set()
        for item in members:
            name = item.filename
            parts = PurePosixPath(name).parts
            if (
                name in names
                or "\\" in name
                or ":" in name
                or name.startswith("/")
                or ".." in parts
                or item.flag_bits & 1
                or (
                    item.file_size > 1024 * 1024
                    and item.file_size > 200 * max(item.compress_size, 1)
                )
            ):
                raise DocumentStorageError("DOCX có cấu trúc nén không được hỗ trợ.")
            names.add(name)
        if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
            raise DocumentStorageError("Nội dung không phải tài liệu Word DOCX.")
        # No extraction. Parse bounded XML with DTD/entities/external references disabled.
        for name in ("[Content_Types].xml", "word/document.xml"):
            if archive.getinfo(name).file_size > min(max_unpacked_bytes, 20 * 1024 * 1024):
                raise DocumentStorageError("Nội dung XML của DOCX vượt giới hạn.")
            root = ElementTree.fromstring(
                archive.read(name), forbid_dtd=True, forbid_entities=True, forbid_external=True
            )
            if name == "[Content_Types].xml":
                main_type = (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document.main+xml"
                )
                if not any(
                    item.attrib.get("PartName") == "/word/document.xml"
                    and item.attrib.get("ContentType") == main_type
                    for item in root
                ):
                    raise DocumentStorageError("DOCX không đúng định dạng Word thông thường.")
            elif root.tag not in {
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                "{http://purl.oclc.org/ooxml/wordprocessingml/main}document",
            }:
                raise DocumentStorageError("DOCX thiếu nội dung Word hợp lệ.")
        if any(name.lower().endswith("vbaproject.bin") for name in names):
            raise DocumentStorageError("DOCX có macro không được hỗ trợ.")


def _validate_content(
    path: Path, file_type: str, max_unpacked_bytes: int, max_entries: int
) -> list[dict[str, str]]:
    try:
        if file_type == "docx":
            _validate_docx(path, max_unpacked_bytes, max_entries)
        elif file_type == "doc":
            with olefile.OleFileIO(str(path), raise_defects=olefile.DEFECT_INCORRECT) as container:
                if not container.exists("WordDocument"):
                    raise DocumentStorageError("Nội dung không phải file Word DOC.")
                if container.get_size("WordDocument") > max_unpacked_bytes:
                    raise DocumentStorageError("Nội dung Word vượt giới hạn.")
        elif file_type == "pdf":
            with path.open("rb") as source:
                header = source.read(16)
                source.seek(max(0, path.stat().st_size - 4096))
                trailer = source.read()
            if not re.match(rb"%PDF-[12]\.\d", header) or b"%%EOF" not in trailer:
                raise DocumentStorageError("Nội dung không đúng cấu trúc file PDF.")
            # Full PDF parsing, encryption/page checks and scan coverage belong to ingestion.
        else:
            raw = path.read_bytes()
            encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            content = raw.decode(encoding)
            if not content.strip() or any(
                unicodedata.category(char) == "Cc" and char not in "\t\r\n\f" for char in content
            ):
                raise DocumentStorageError("TXT phải là văn bản UTF-8 hoặc UTF-16 hợp lệ.")
            return [{"code": "text_encoding", "encoding": encoding}]
    except DocumentStorageError:
        raise
    except (
        ValueError,
        OSError,
        EOFError,
        RuntimeError,
        DefusedXmlException,
        ElementTree.ParseError,
        zipfile.BadZipFile,
    ) as exc:
        raise DocumentStorageError("File bị hỏng, mã hóa hoặc không đúng định dạng.") from exc
    return []


class LocalDocumentStorage:
    def __init__(self, root: Path, *, max_unpacked_bytes: int, max_entries: int):
        self.root = root.resolve()
        self.max_unpacked_bytes, self.max_entries = max_unpacked_bytes, max_entries

    def staging_directory(self, owner: UUID, document: UUID, version: UUID | None = None) -> Path:
        base = (self.root / ".ingestion").resolve()
        candidate = (base / str(owner) / str(document)).resolve()
        if version is not None:
            candidate = (candidate / str(version)).resolve()
        if not base.is_relative_to(self.root) or not candidate.is_relative_to(base):
            raise DocumentStorageError("Vùng xử lý tài liệu không hợp lệ.")
        return candidate

    async def cleanup_staging(self, owner: UUID, document: UUID) -> None:
        # Resolve/check the exact owner/document subtree before recursive removal.
        target = self.staging_directory(owner, document)
        if not target.exists():
            return
        task = asyncio.create_task(asyncio.to_thread(shutil.rmtree, target))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise DocumentStorageError("Khóa lưu trữ không hợp lệ.")
        candidate = self.root.joinpath(*key.split("/"))
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise DocumentStorageError("Khóa lưu trữ không hợp lệ.")
        return resolved

    def _save(self, source: BinaryIO, key: str, max_bytes: int) -> StoredUpload:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self._path(key)
        created = False
        try:
            digest, size = hashlib.sha256(), 0
            source.seek(0)
            with destination.open("xb") as target:
                created = True
                os.chmod(destination, 0o600)
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > max_bytes:
                        raise DocumentStorageError(
                            "File vượt giới hạn dung lượng.",
                            status_code=413,
                            code="document_too_large",
                        )
                    target.write(block)
                    digest.update(block)
            if size == 0:
                raise DocumentStorageError("File rỗng không được hỗ trợ.")
            warnings = _validate_content(
                destination, destination.suffix[1:], self.max_unpacked_bytes, self.max_entries
            )
            return StoredUpload(key, size, digest.hexdigest(), warnings)
        except BaseException:
            if created:
                self._delete(key)
            raise

    async def save(self, source: BinaryIO, key: str, max_bytes: int) -> StoredUpload:
        task = asyncio.create_task(asyncio.to_thread(self._save, source, key, max_bytes))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                await self.delete(key)
            raise

    def download_path(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise DocumentStorageError(
                "Không tìm thấy file đã lưu.", status_code=404, code="document_file_missing"
            )
        return path

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete, key)

    async def publish_parsed(self, source: Path, key: str) -> None:
        task = asyncio.create_task(asyncio.to_thread(self._publish_parsed, source, key))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def _publish_parsed(self, source: Path, key: str) -> None:
        resolved = source.resolve()
        staging = (self.root / ".ingestion").resolve()
        if not staging.is_relative_to(self.root) or not resolved.is_relative_to(staging):
            raise DocumentStorageError("Bản trích xuất không thuộc vùng xử lý riêng.")
        destination = self._path(key)
        if destination.name != "parsed.json":
            raise DocumentStorageError("Khóa bản trích xuất không hợp lệ.")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self._path(key)
        os.chmod(resolved, 0o600)
        os.replace(resolved, destination)

    def _delete(self, key: str) -> None:
        # Delete a validated file, then empty parent directories; never recursively delete.
        path = self._path(key)
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != self.root and parent.is_relative_to(self.root):
            try:
                parent.rmdir()
            except FileNotFoundError:
                pass
            except OSError:
                break  # A sibling version/file still exists, or its directory is not removable.
            parent = parent.parent
