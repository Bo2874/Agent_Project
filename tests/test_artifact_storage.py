import hashlib
import io
import zipfile
from uuid import uuid4

import pytest

from backend.services.artifacts.storage import ArtifactStorage, ArtifactStorageError
from backend.services.artifacts.validation import validate_archive


def key():
    return f"{uuid4()}/{uuid4()}/{uuid4()}/content.xlsx"


def test_storage_streams_hashes_and_never_overwrites(tmp_path):
    storage = ArtifactStorage(tmp_path / "private")
    location = key()
    stored = storage.put(io.BytesIO(b"first version"), location, max_bytes=30)
    assert stored.sha256 == hashlib.sha256(b"first version").hexdigest()
    assert stored.size_bytes == 13
    with pytest.raises(ArtifactStorageError):
        storage.put(io.BytesIO(b"replacement"), location, max_bytes=30)
    assert storage.path(location).read_bytes() == b"first version"


def test_oversize_rolls_back_partial_file(tmp_path):
    storage = ArtifactStorage(tmp_path)
    location = key()
    with pytest.raises(ArtifactStorageError):
        storage.put(io.BytesIO(b"a" * 100), location, max_bytes=99)
    assert not storage.path(location).exists()


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", "a\\b", "a:b"])
def test_traversal_and_zip_bomb_rejected(tmp_path, name):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, b"a" * 10000)
    target = tmp_path / "bad.xlsx"
    target.write_bytes(output.getvalue())
    with pytest.raises(ArtifactStorageError):
        validate_archive(target, max_unpacked_bytes=1000, max_entries=2000)
    assert list(tmp_path.glob("escape")) == []


def test_archive_symlink_and_duplicate_rejected(tmp_path):
    target = tmp_path / "link.xlsx"
    item = zipfile.ZipInfo("xl/link")
    item.create_system = 3
    item.external_attr = 0o120777 << 16
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(item, "../../outside")
    with pytest.raises(ArtifactStorageError):
        validate_archive(target)


def test_storage_rejects_host_paths(tmp_path):
    storage = ArtifactStorage(tmp_path)
    for location in ("../outside.xlsx", "C:/outside.xlsx", "a/b/c/content.html"):
        with pytest.raises(ArtifactStorageError):
            storage.put(io.BytesIO(b"x"), location, max_bytes=10)
