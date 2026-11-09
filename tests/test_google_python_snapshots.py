import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from artifact_runtime.google_types import PythonGoogleInput
from backend.schemas.google import DOC_MIME, SHEET_MIME
from backend.services.artifacts.storage import ArtifactStorage, StoredArtifact
from backend.services.google.client import DRIVE_URL, GoogleHTTP
from backend.services.google.errors import GoogleServiceError
from backend.services.google.snapshots import GoogleSnapshots


@asynccontextmanager
async def sessions():
    yield None


class Access:
    def __init__(self, mime=DOC_MIME):
        self.mime = mime
        self.version = "7"
        self.change_during_read = False
        self.calls = []
        self.credentials = SimpleNamespace(http=self)

    async def authorize(self, session, context, ref, action):
        self.calls.append(action)
        return SimpleNamespace(
            ref=ref,
            token="private-token",
            mime_type=self.mime,
            grant_generation=2,
            connection_generation=3,
        )

    async def metadata(self, file_id, token):
        return {
            "id": file_id,
            "name": "Report",
            "mimeType": self.mime,
            "version": self.version,
            "capabilities": {"canEdit": True},
        }

    async def request(self, method, url, **kwargs):
        if self.change_during_read:
            self.version = "8"
        if self.mime == DOC_MIME:
            return {
                "documentId": "file1",
                "revisionId": "rev7",
                "body": {
                    "content": [
                        {
                            "startIndex": 1,
                            "endIndex": 5,
                            "paragraph": {"elements": [{"textRun": {"content": "A😀\n"}}]},
                        }
                    ]
                },
            }
        return {
            "spreadsheetId": "file1",
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1", "index": 0}}],
        }

    async def download_bytes(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        return b"binary file"


def source(mode="native", **kwargs):
    return PythonGoogleInput(
        ref={"connection_id": uuid4(), "context_id": uuid4(), "file_id": "file1"},
        mode=mode,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_doc_snapshot_preserves_native_indices_and_no_credentials(tmp_path):
    access = Access()
    storage = ArtifactStorage(tmp_path)
    service = GoogleSnapshots(access, sessions, storage)
    captured = await service.capture(SimpleNamespace(user_id=uuid4()), source())
    stored = StoredArtifact(**captured["stored"])
    data = json.loads(storage.path(stored.key).read_bytes())
    assert data["body"]["content"][0]["endIndex"] == 5
    assert data["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] == "A😀\n"
    assert captured["revision_id"] == "rev7"
    assert "private-token" not in json.dumps(captured)
    await service.validate(SimpleNamespace(user_id=uuid4()), captured)
    access.version = "8"
    with pytest.raises(GoogleServiceError, match="changed"):
        await service.validate(SimpleNamespace(user_id=uuid4()), captured)


@pytest.mark.asyncio
async def test_version_changes_during_capture_are_rejected(tmp_path):
    access = Access()
    access.change_during_read = True
    with pytest.raises(GoogleServiceError, match="changed"):
        await GoogleSnapshots(access, sessions, ArtifactStorage(tmp_path)).capture(
            SimpleNamespace(user_id=uuid4()), source()
        )
    assert not list(tmp_path.rglob("content.*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,mime,extra",
    [
        ("download", "application/pdf", {}),
        ("export", DOC_MIME, {"export_mime": "application/pdf"}),
    ],
)
async def test_binary_sources_are_staged_without_native_reconstruction(tmp_path, mode, mime, extra):
    storage = ArtifactStorage(tmp_path)
    captured = await GoogleSnapshots(Access(mime), sessions, storage).capture(
        SimpleNamespace(user_id=uuid4()), source(mode, **extra)
    )
    assert storage.path(captured["stored"]["key"]).read_bytes() == b"binary file"
    assert captured["stored"]["mime_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_native_download_and_doc_range_are_rejected(tmp_path):
    service = GoogleSnapshots(Access(), sessions, ArtifactStorage(tmp_path))
    for item in [source("download"), source(range_a1="A1:B2")]:
        with pytest.raises(GoogleServiceError):
            await service.capture(SimpleNamespace(user_id=uuid4()), item)


@pytest.mark.asyncio
async def test_download_bounds_redirect_and_origin():
    def handler(request):
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, content=b"12345")

    http = GoogleHTTP(httpx.MockTransport(handler))
    with pytest.raises(GoogleServiceError):
        await http.download_bytes(DRIVE_URL + "/files/file1", token="secret", max_bytes=4)
    with pytest.raises(GoogleServiceError):
        await http.download_bytes("https://evil.test/file", token="secret", max_bytes=20)
    redirect = GoogleHTTP(
        httpx.MockTransport(
            lambda request: httpx.Response(302, headers={"Location": "https://evil.test/file"})
        )
    )
    with pytest.raises(GoogleServiceError):
        await redirect.download_bytes(DRIVE_URL + "/files/file1", token="secret", max_bytes=20)


@pytest.mark.asyncio
async def test_sheet_range_is_explicit_and_oversized_snapshot_not_stored(tmp_path):
    access = Access(SHEET_MIME)
    original = access.request
    seen = []

    async def request(method, url, **kwargs):
        seen.append(kwargs["params"])
        return await original(method, url, **kwargs)

    access.request = request
    service = GoogleSnapshots(access, sessions, ArtifactStorage(tmp_path))
    await service.capture(SimpleNamespace(user_id=uuid4()), source(range_a1="Sheet1!A1:B10"))
    assert seen[0]["ranges"] == "Sheet1!A1:B10"

    async def huge(*args, **kwargs):
        return {"spreadsheetId": "file1", "sheets": [], "padding": "x" * 2097152}

    access.request = huge
    with pytest.raises(GoogleServiceError, match="2 MiB"):
        await service.capture(SimpleNamespace(user_id=uuid4()), source())


@pytest.mark.asyncio
async def test_revocation_after_read_prevents_staging(tmp_path):
    access = Access()
    authorize = access.authorize
    count = 0

    async def revoked(*args):
        nonlocal count
        count += 1
        if count > 1:
            raise GoogleServiceError(404, "Google file permission unavailable")
        return await authorize(*args)

    access.authorize = revoked
    with pytest.raises(GoogleServiceError, match="permission"):
        await GoogleSnapshots(access, sessions, ArtifactStorage(tmp_path)).capture(
            SimpleNamespace(user_id=uuid4()), source()
        )
    assert not list(tmp_path.rglob("content.*"))
