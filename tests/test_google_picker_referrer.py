import os
import shutil
import subprocess
from types import SimpleNamespace

import httpx
from test_plugins_ui import page_app

from backend.routes import pages


async def test_picker_page_sends_origin_without_url_secrets(monkeypatch):
    application = page_app(monkeypatch, "user")
    monkeypatch.setattr(
        pages, "get_settings", lambda: SimpleNamespace(google_connector_enabled=True)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.get("/connectors?code=private-code&state=private-state")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    result = subprocess.run(
        [shutil.which("node"), "tests/google_picker_referrer_browser.cjs"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        env={**os.environ, "PICKER_TEST_REFERRER_POLICY": reply.headers["referrer-policy"]},
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_google_oauth_error_redirect_keeps_no_referrer():
    from starlette.requests import Request

    from backend.routes.connections import google_callback

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/connectors/google/callback",
            "query_string": b"error=access_denied&code=private-code&state=private-state",
            "headers": [],
        }
    )
    reply = await google_callback(request, None, None)
    assert reply.headers["referrer-policy"] == "no-referrer"
    assert reply.headers["cache-control"] == "no-store"
    assert reply.headers["location"] == "/connectors?google=reconnect"
