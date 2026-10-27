import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from backend.core import security
from backend.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from backend.database import get_db_session
from backend.routes import auth


def test_password_is_hashed_and_can_be_verified() -> None:
    password = "a-valid-password"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password, password_hash)
    assert not verify_password("wrong-password", password_hash)


def test_access_token_contains_user_identity() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "user")

    assert decode_access_token(token) == user_id
    assert decode_access_token(f"{token}changed") is None


@pytest.fixture
def refresh_context(monkeypatch):
    settings = SimpleNamespace(
        auth_secret_key=SecretStr("test-session-refresh-secret-at-least-32-characters"),
        auth_token_expire_minutes=60,
        auth_cookie_secure=True,
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    user = SimpleNamespace(id=uuid.uuid4(), role="user", is_active=True)

    class Session:
        async def get(self, model, user_id):
            return user if user_id == user.id else None

    async def session_dependency():
        yield Session()

    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_db_session] = session_dependency
    return app, user, settings


def session_token(settings, user_id, expires_at):
    return jwt.encode(
        {"sub": str(user_id), "role": "admin", "exp": expires_at},
        settings.auth_secret_key.get_secret_value(),
        algorithm="HS256",
    )


@pytest.mark.parametrize("secure", [True, False])
async def test_refresh_renews_expiry_and_preserves_private_cookie(refresh_context, secure) -> None:
    app, user, settings = refresh_context
    settings.auth_cookie_secure = secure
    old_expiry = datetime.now(UTC) + timedelta(seconds=30)
    token = session_token(settings, user.id, old_expiry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        client.cookies.set("access_token", token)
        response = await client.post("/api/auth/refresh", headers={"X-Session-Activity": "1"})

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    for attribute in ("HttpOnly", "SameSite=lax", "Path=/", "Max-Age=3600"):
        assert attribute in cookie
    assert ("Secure" in cookie) is secure
    renewed = response.cookies.get("access_token")
    claims = jwt.decode(renewed, settings.auth_secret_key.get_secret_value(), algorithms=["HS256"])
    assert claims["sub"] == str(user.id)
    # Authorization comes from the current database user, not stale JWT role claims.
    assert claims["role"] == "user"
    assert claims["exp"] > old_expiry.timestamp() + 3500
    assert decode_access_token(renewed) == user.id


@pytest.mark.parametrize(
    "invalid_session", ["missing", "invalid", "expired", "inactive", "deleted"]
)
async def test_refresh_rejects_invalid_sessions(refresh_context, invalid_session) -> None:
    app, user, settings = refresh_context
    expires_at = datetime.now(UTC) + timedelta(minutes=1)
    user_id = user.id
    if invalid_session == "expired":
        expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if invalid_session == "inactive":
        user.is_active = False
    if invalid_session == "deleted":
        user_id = uuid.uuid4()
    token = session_token(settings, user_id, expires_at)
    if invalid_session == "invalid":
        token += "tampered"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        if invalid_session != "missing":
            client.cookies.set("access_token", token)
        response = await client.post("/api/auth/refresh", headers={"X-Session-Activity": "1"})

    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def test_polling_and_requests_without_activity_do_not_refresh(refresh_context) -> None:
    app, user, settings = refresh_context
    token = session_token(settings, user.id, datetime.now(UTC) + timedelta(minutes=1))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        client.cookies.set("access_token", token)
        # /me uses the same authentication dependency as background document polling.
        user.email = "test@example.com"
        user.full_name = None
        user.created_at = datetime.now(UTC)
        polling = await client.get("/api/auth/me")
        missing_header = await client.post("/api/auth/refresh")
        invalid_header = await client.post("/api/auth/refresh", headers={"X-Session-Activity": "0"})

    assert polling.status_code == 200
    assert missing_header.status_code == invalid_header.status_code == 403
    assert all(
        "set-cookie" not in response.headers
        for response in (polling, missing_header, invalid_header)
    )


async def test_logout_clears_cookie_after_refresh(refresh_context) -> None:
    app, user, _ = refresh_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        client.cookies.set(
            "access_token", create_access_token(user.id, user.role), domain="test.local", path="/"
        )
        assert (
            await client.post("/api/auth/refresh", headers={"X-Session-Activity": "1"})
        ).status_code == 204
        response = await client.post("/api/auth/logout")
        after_logout = await client.post("/api/auth/refresh", headers={"X-Session-Activity": "1"})

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert after_logout.status_code == 401
