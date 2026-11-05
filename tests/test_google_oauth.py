import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import pytest
from google_fixtures import SCOPE, connect, google_db  # noqa: F401
from sqlalchemy import select


def test_empty_client_secrets_are_not_configured():
    from pydantic import SecretStr

    from backend.core.config import Settings
    from backend.services.google.oauth import GoogleOAuth

    settings = Settings(
        _env_file=None,
        db_password="fixture",
        auth_secret_key="s" * 32,
        google_connector_enabled=True,
        google_client_id="client",
        google_client_secret="",
        google_credential_encryption_keys="",
    )
    assert not GoogleOAuth(settings).configured()
    assert not GoogleOAuth(
        settings.model_copy(update={"google_client_secret": SecretStr("valid")})
    ).configured()


async def test_state_single_use_owner_ttl_pkce_and_no_secret_view(google_db, caplog):  # noqa: F811
    from backend.models.google_connection import GoogleOAuthState
    from backend.services.google.errors import GoogleServiceError
    from backend.services.google.oauth import GoogleOAuth

    db = google_db
    service = GoogleOAuth(db.settings, transport=db.transport)
    async with db.sessions() as session:
        start = await service.begin(session, db.owner)
        query = parse_qs(start.authorization_url.split("?", 1)[1])
        assert query["scope"] == [SCOPE] and query["access_type"] == ["offline"]
        assert query["code_challenge_method"] == ["S256"]
        state = query["state"][0]
        row = await session.scalar(select(GoogleOAuthState))
        assert row.state_hash == hashlib.sha256(state.encode()).hexdigest()
        assert 590 < (row.expires_at - datetime.now(UTC)).total_seconds() <= 600
        assert b"code_verifier" not in row.ciphertext
        with pytest.raises(GoogleServiceError):
            await service.complete(session, db.other, state=state, code="code-canary")
        view = await service.complete(session, db.owner, state=state, code="code-canary")
        assert view.status == "connected" and view.offline_ready
        assert view.account_email == "fixture@example.com"
        assert not any(
            word in view.model_dump_json() + caplog.text
            for word in (
                "access-canary",
                "refresh-canary",
                "code-canary",
                "client-secret-canary",
                "forged-token-response",
            )
        )
        with pytest.raises(GoogleServiceError):
            await service.complete(session, db.owner, state=state, code="code-canary")
        start = await service.begin(session, db.owner)
        state = parse_qs(start.authorization_url.split("?", 1)[1])["state"][0]
        row = await session.get(GoogleOAuthState, hashlib.sha256(state.encode()).hexdigest())
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        with pytest.raises(GoogleServiceError):
            await service.complete(session, db.owner, state=state, code="expired-code")


@pytest.mark.parametrize("reason", ["offline", "scope"])
async def test_missing_offline_token_or_scope_needs_reconnect(google_db, reason):  # noqa: F811
    db = google_db
    db.provider.no_refresh = reason == "offline"
    db.provider.scope = "" if reason == "scope" else SCOPE
    if reason == "scope":
        from backend.services.google.errors import GoogleServiceError

        with pytest.raises(GoogleServiceError, match="scope|permission"):
            await connect(db)
    else:
        _, view = await connect(db)
        assert view.status == "reconnect" and not view.offline_ready


async def test_oauth_routes_require_owner_origin_and_redact_callback(google_db, monkeypatch):  # noqa: F811
    import logging

    import httpx
    from fastapi import FastAPI

    from backend.dependencies import get_db_session, get_optional_user
    from backend.models import User
    from backend.routes import connections
    from backend.services.google.oauth import GoogleOAuth

    db = google_db
    app = FastAPI()
    connections.install(app)
    monkeypatch.setattr(connections, "get_settings", lambda: db.settings)
    monkeypatch.setattr(
        connections, "oauth_service", lambda: GoogleOAuth(db.settings, transport=db.transport)
    )

    async def actor():
        async with db.sessions() as session:
            return await session.get(User, db.owner)

    async def database():
        async with db.sessions() as session:
            yield session

    app.dependency_overrides[get_optional_user] = actor
    app.dependency_overrides[get_db_session] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post(
            "/api/connections/google", headers={"Origin": "https://evil.example"}
        )
        assert denied.status_code == 403
        result = await client.post("/api/connections/google")
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        state = parse_qs(result.json()["authorization_url"].split("?", 1)[1])["state"][0]
        callback = await client.get(
            "/connectors/google/callback", params={"code": "code-canary", "state": state}
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/connectors?google=connected"
        status = await client.get("/api/connections/google")
        identity = status.json()["connections"][0]["id"]
        assert "canary" not in status.text
        assert (await client.delete(f"/api/connections/google/{identity}")).status_code == 204
        assert (await client.get("/api/connections/google")).json()["connections"][0][
            "status"
        ] == "disconnected"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s "%s %s HTTP/%s" %d',
        (
            "local",
            "GET",
            "/connectors/google/callback?code=code-canary&state=state-canary",
            "1.1",
            303,
        ),
        None,
    )
    connections.GoogleCallbackLogFilter().filter(record)
    assert "canary" not in record.getMessage()
