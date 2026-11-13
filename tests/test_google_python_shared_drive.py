import httpx
import pytest

from backend.services.google.client import GoogleHTTP


@pytest.mark.asyncio
@pytest.mark.parametrize("file_id", [None, "selected-file"])
async def test_resumable_upload_declares_shared_drive_support(file_id):
    async def handle(request):
        if request.method in {"POST", "PATCH"}:
            assert request.url.params["supportsAllDrives"] == "true"
            return httpx.Response(200, headers={"Location": str(request.url) + "&upload_id=qa"})
        return httpx.Response(200, json={"id": file_id or "created-file"})

    result = await GoogleHTTP(httpx.MockTransport(handle)).upload_bytes(
        token="synthetic",
        metadata={"name": "report.pdf"},
        content=b"bytes",
        source_mime="application/pdf",
        file_id=file_id,
    )
    assert result["id"] == (file_id or "created-file")
