"""Dedicated PostgreSQL schemas and deterministic Google HTTP, never real accounts."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.core.config import Settings
from backend.models import Base, Conversation, User

SCOPE = "https://www.googleapis.com/auth/drive.file"


class FakeGoogle:
    def __init__(self):
        self.calls = []
        self.account = "permission-account-a"
        self.refresh_count = 0
        self.invalid_grant = False
        self.no_refresh = False
        self.scope = SCOPE

    async def handle(self, request):
        self.calls.append((request.method, request.url.path))
        if str(request.url) == "https://oauth2.googleapis.com/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                self.refresh_count += 1
                if self.invalid_grant:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(
                    200, json={"access_token": "fresh-access-canary", "expires_in": 3600}
                )
            assert form["code_verifier"][0] and form["redirect_uri"] == [
                "http://127.0.0.1:8000/connectors/google/callback"
            ]
            payload = {
                "access_token": "access-canary",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": self.scope,
                "email": "forged-token-response@example.com",
            }
            if not self.no_refresh:
                payload["refresh_token"] = "refresh-canary"
            return httpx.Response(200, json=payload)
        if request.url.path == "/drive/v3/about":
            return httpx.Response(
                200,
                json={
                    "user": {
                        "permissionId": self.account,
                        "emailAddress": "fixture@example.com",
                        "displayName": "Fixture account",
                    }
                },
            )
        raise AssertionError(f"Unexpected fixture endpoint {request.method} {request.url.path}")


@pytest.fixture
async def google_db():
    url = os.environ.get("GOOGLE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires isolated GOOGLE_TEST_DATABASE_URL")
    from sqlalchemy.engine import make_url

    assert make_url(url).database.endswith("_test")
    schema = "test_workspace_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    async with engine.begin() as connection:
        # Public has migrated tables too: do not let has_table() find those through
        # search_path and silently reuse them instead of creating this test schema.
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, other = uuid4(), uuid4()
    conversation = uuid4()
    async with sessions() as session:
        session.add_all(
            [
                User(id=identity, email=f"{identity}@example.com", password_hash="fixture")
                for identity in (owner, other)
            ]
        )
        await session.flush()
        session.add(Conversation(id=conversation, user_id=owner))
        await session.commit()
    provider = FakeGoogle()
    settings = Settings(
        _env_file=None,
        db_password="fixture",
        auth_secret_key="google-fixture-secret-" * 3,
        google_connector_enabled=True,
        google_client_id="fixture-client.apps.googleusercontent.com",
        google_client_secret="client-secret-canary",
        google_redirect_uri="http://127.0.0.1:8000/connectors/google/callback",
        google_credential_encryption_keys=Fernet.generate_key().decode(),
    )
    try:
        yield SimpleNamespace(
            sessions=sessions,
            owner=owner,
            other=other,
            conversation=conversation,
            provider=provider,
            settings=settings,
            transport=httpx.MockTransport(provider.handle),
        )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def connect(db):
    from backend.services.google.oauth import GoogleOAuth

    service = GoogleOAuth(db.settings, transport=db.transport)
    async with db.sessions() as session:
        start = await service.begin(session, db.owner)
        state = parse_qs(httpx.URL(start.authorization_url).query.decode())["state"][0]
        view = await service.complete(session, db.owner, state=state, code="code-canary")
    return service, view


async def expire(db, identity):
    from backend.models.google_connection import GoogleConnection

    async with db.sessions() as session:
        row = await session.get(GoogleConnection, identity)
        row.expires_at = datetime(2000, 1, 1, tzinfo=UTC)
        await session.commit()
