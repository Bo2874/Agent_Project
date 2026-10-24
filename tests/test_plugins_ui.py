from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from agent_core.plugins import load_plugin_manifest
from backend.database import get_db_session
from backend.dependencies import get_optional_user
from backend.models import Plugin, PluginGrant, User
from backend.routes import pages, plugins
from backend.services import plugin_credentials
from backend.services import plugins as service


def page_app(monkeypatch, role, enabled=True):
    application = FastAPI()
    application.include_router(pages.router)
    # Templates use url_for(static); keep the actual static mount.
    from fastapi.staticfiles import StaticFiles

    application.mount(
        "/static", StaticFiles(directory=pages.FRONTEND_DIR / "static"), name="static"
    )
    actor = (
        SimpleNamespace(
            id=uuid4(), role=role, email="ui@example.com", full_name="UI", is_active=True
        )
        if role
        else None
    )
    application.dependency_overrides[get_optional_user] = lambda: actor
    monkeypatch.setattr(
        pages,
        "get_settings",
        lambda: SimpleNamespace(v2_features=SimpleNamespace(is_enabled=lambda _: enabled)),
    )
    return application


@pytest.mark.parametrize("path", ["/plugins", "/admin/plugins", "/plugins/oauth/callback"])
async def test_plugin_pages_require_login(monkeypatch, path):
    application = page_app(monkeypatch, None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.get(path)
    assert reply.status_code == 303 and reply.headers["location"] == "/login"
    assert reply.headers["referrer-policy"] == "no-referrer"
    assert reply.headers["cache-control"] == "no-store"


async def test_user_cannot_open_admin_plugin_page(monkeypatch):
    application = page_app(monkeypatch, "user")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.get("/admin/plugins")
    assert reply.status_code == 403
    assert "data-plugin-register" not in reply.text


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_disabled_plugin_page_has_no_mutation_controls(monkeypatch, role):
    application = page_app(monkeypatch, role, enabled=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.get("/plugins")
    assert reply.status_code == 200
    assert "Plugins chưa được bật" in reply.text
    assert "data-plugin-controls" not in reply.text and "data-plugin-register" not in reply.text


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_plugin_page_exposes_only_correct_role_controls(monkeypatch, role):
    application = page_app(monkeypatch, role)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.get("/plugins")
    assert reply.status_code == 200
    assert 'data-plugin-panel="mine"' in reply.text
    assert ("data-plugin-register" in reply.text) == (role == "admin")
    assert reply.headers["cache-control"] == "no-store"
    assert reply.headers["referrer-policy"] == "no-referrer"


async def test_callback_page_does_not_reflect_authorization_material(monkeypatch):
    application = page_app(monkeypatch, "user")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.get("/plugins/oauth/callback?code=private-code&state=private-state")
    assert reply.status_code == 200
    assert "private-code" not in reply.text and "private-state" not in reply.text
    assert reply.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("role,enabled,expected", [(None, True, 401), ("user", False, 404)])
async def test_browser_oauth_completion_remains_authenticated_and_feature_gated(
    monkeypatch, role, enabled, expected
):
    application = FastAPI()
    application.include_router(plugins.router)
    application.dependency_overrides[get_optional_user] = lambda: (
        User(id=uuid4(), role=role, is_active=True) if role else None
    )
    application.dependency_overrides[get_db_session] = lambda: None
    monkeypatch.setattr(
        plugins,
        "get_settings",
        lambda: SimpleNamespace(v2_features=SimpleNamespace(is_enabled=lambda _: enabled)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.post(
            "/api/plugins/oauth/complete", json={"state": "state", "code": "code"}
        )
    assert reply.status_code == expected


async def test_browser_oauth_completion_uses_current_actor_and_owner_bound_state(monkeypatch):
    actor = User(id=uuid4(), role="user", is_active=True)
    calls = []

    async def complete(session, current_actor, nonce, code, issuer):
        calls.append((current_actor.id, nonce, code, issuer))
        return {"catalog_version": "1.0", "auth_version": 3, "credential_references": []}

    application = FastAPI()
    application.include_router(plugins.router)
    application.dependency_overrides[get_optional_user] = lambda: actor
    application.dependency_overrides[get_db_session] = lambda: None
    application.dependency_overrides[plugins.require_plugin_feature] = lambda: None
    monkeypatch.setattr(plugin_credentials, "complete_state", complete)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        reply = await client.post(
            "/api/plugins/oauth/complete",
            json={
                "state": "synthetic-state",
                "code": "synthetic-code",
                "issuer": "https://issuer.example",
            },
        )
    assert reply.status_code == 200
    assert calls == [(actor.id, "synthetic-state", "synthetic-code", "https://issuer.example")]
    assert "synthetic-state" not in reply.text and "synthetic-code" not in reply.text


async def test_user_credential_requirements_have_no_host_or_secret_fields(monkeypatch):
    manifest = load_plugin_manifest(pages.FRONTEND_DIR.parent / "plugins/manifests/demo.json")
    data = manifest.model_dump(mode="json")
    data["servers"][0].update(
        transport="streamable_http",
        credentials=[{"slot": "token", "kind": "bearer_token"}],
        connection_profile_ref="private.host.profile",
    )
    plugin = Plugin(
        id=manifest.id,
        namespace=manifest.namespace,
        version=manifest.version,
        policy_version=manifest.policy_version,
        enabled=True,
        manifest_json=data,
    )
    grant = PluginGrant(
        enabled=True, manifest_version=manifest.version, capability_ids=["demo.tool.main.calculate"]
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(plugin, grant)]))
    )
    monkeypatch.setattr(service, "_state", AsyncMock(return_value=None))
    monkeypatch.setattr(
        service, "_context", AsyncMock(return_value={"catalog_version": "1.0", "auth_version": 1})
    )
    result = await service.user_plugins(session, User(id=uuid4(), role="user", is_active=True))
    assert set(result.model_dump()["plugins"][0]["credential_requirements"][0]) == {
        "server_id",
        "slot",
        "kind",
        "transport",
    }
    assert "private.host.profile" not in result.model_dump_json()
