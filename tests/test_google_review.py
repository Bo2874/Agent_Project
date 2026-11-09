"""Bounded HTTP envelope regression, retained from the original Google connector."""

import httpx
import pytest


@pytest.mark.parametrize("body", [b"not-json", b"[]"])
async def test_invalid_google_success_body_is_rejected(body):
    from backend.services.google.client import SHEETS_URL, GoogleHTTP
    from backend.services.google.errors import GoogleServiceError

    client = GoogleHTTP(httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    with pytest.raises(GoogleServiceError):
        await client.request("GET", SHEETS_URL + "/sheet-a/values/A1:B10", token="fixture")

