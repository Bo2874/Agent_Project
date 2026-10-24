from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from agent_core.plugins import PluginManifest, load_plugin_manifest
from backend.database import get_db_session
from backend.dependencies import get_optional_user
from backend.models import Plugin, PluginConnection, PluginCredentialReference, PluginGrant, User
from backend.routes.plugins import require_plugin_feature, router
from backend.schemas.plugin import (
    ConnectionEnabledRequest,
    CreateConnectionRequest,
    CreateCredentialReferenceRequest,
    CredentialReferenceItem,
    GrantRequest,
)
from backend.services.plugins import (
    PluginServiceError,
    _effective_scope,
    _manifest,
    connection_status,
    grant_scope,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def manifest():
    data = load_plugin_manifest(ROOT / "plugins/manifests/demo.json").model_dump(mode="json")
    data["servers"][0]["credentials"] = [{"slot": "token", "kind": "bearer_token"}]
    return PluginManifest.model_validate(data)


@pytest.mark.parametrize("value", [True, -1, 1.5, "1", None])
def test_auth_version_requires_nonnegative_integer(value):
    with pytest.raises(ValidationError):
        ConnectionEnabledRequest(enabled=True, expected_auth_version=value)


@pytest.mark.parametrize("value", [1, "true", "false", None])
def test_enabled_requires_boolean(value):
    with pytest.raises(ValidationError):
        ConnectionEnabledRequest(enabled=value, expected_auth_version=0)


@pytest.mark.parametrize("field", ["user_id", "owner_id", "enabled", "credentials", "api_key"])
def test_user_cannot_inject_owner_credentials_or_enable_on_create(field):
    with pytest.raises(ValidationError):
        CreateConnectionRequest.model_validate(
            {"plugin_id": "builtin.demo", "expected_auth_version": 0, field: "forged"}
        )


@pytest.mark.parametrize("scope", [[], ["demo.tool.main.calculate"] * 2])
def test_enabled_grant_requires_explicit_unique_scope(scope):
    with pytest.raises(ValidationError):
        GrantRequest(enabled=True, capability_ids=scope, expected_auth_version=0)


@pytest.mark.parametrize("value", ["sk-secret-raw", "https://vault/secrets/token", "../token"])
def test_credential_reference_accepts_only_opaque_uuid(value):
    with pytest.raises(ValidationError):
        CreateCredentialReferenceRequest(
            server_id="main",
            slot="token",
            provider="vault",
            secret_id=value,
            expected_auth_version=0,
        )


def test_credential_response_does_not_disclose_store_binding():
    secret_id = uuid4()
    row = PluginCredentialReference(
        id=uuid4(),
        connection_id=uuid4(),
        user_id=uuid4(),
        plugin_id="builtin.demo",
        server_id="main",
        slot="token",
        provider="vault",
        secret_id=secret_id,
        manifest_version="1.0.0",
        created_at=datetime.now(UTC),
        revoked_at=None,
    )
    response = CredentialReferenceItem.model_validate(row).model_dump_json()
    assert str(secret_id) not in response
    assert "provider" not in response and "user_id" not in response


@pytest.mark.parametrize(
    "role,scope",
    [
        ("user", ("demo.tool.main.admin_runtime",)),
        ("user", ("demo.tool.main.nonexistent",)),
        ("user", ("demo.skill.multi_step_calculation",)),
        ("unknown", ("demo.tool.main.calculate",)),
    ],
)
def test_grant_cannot_bypass_role_declarations_or_skill_dependencies(manifest, role, scope):
    with pytest.raises(PluginServiceError) as failure:
        grant_scope(manifest, role, scope)
    assert failure.value.status_code == 422


def test_explicit_skill_scope_is_canonical_and_survives_role_check(manifest):
    scope = ("demo.tool.main.calculate", "demo.skill.multi_step_calculation")
    assert grant_scope(manifest, "user", scope) == tuple(sorted(scope))
    assert _effective_scope(manifest, "user", list(scope)) == tuple(sorted(scope))


def test_role_demotion_removes_admin_capability_and_dangling_skill(manifest):
    scope = ["demo.tool.main.admin_runtime", "demo.skill.multi_step_calculation"]
    assert _effective_scope(manifest, "user", scope) == ()
    assert _effective_scope(manifest, "admin", scope) == ("demo.tool.main.admin_runtime",)


@pytest.mark.parametrize(
    "change,expected",
    [
        ("none", "configured"),
        ("plugin_disabled", "plugin_disabled"),
        ("missing_grant", "grant_revoked"),
        ("grant_revoked", "grant_revoked"),
        ("role_demoted", "grant_revoked"),
        ("stale_grant", "version_mismatch"),
        ("stale_connection", "version_mismatch"),
        ("missing_reference", "credentials_missing"),
        ("revoked_reference", "credentials_missing"),
        ("stale_reference", "credentials_missing"),
        ("connection_disabled", "connection_disabled"),
    ],
)
def test_effective_connection_requires_all_authorization_layers(manifest, change, expected):
    plugin = Plugin(enabled=True, version=manifest.version)
    grant = PluginGrant(
        enabled=True,
        manifest_version=manifest.version,
        capability_ids=["demo.tool.main.calculate"],
    )
    connection = PluginConnection(enabled=True, manifest_version=manifest.version)
    reference = PluginCredentialReference(
        server_id="main",
        slot="token",
        manifest_version=manifest.version,
        revoked_at=None,
    )
    refs = [reference]
    if change == "plugin_disabled":
        plugin.enabled = False
    elif change == "missing_grant":
        grant = None
    elif change == "grant_revoked":
        grant.enabled = False
    elif change == "role_demoted":
        grant.capability_ids = ["demo.tool.main.admin_runtime"]
    elif change == "stale_grant":
        grant.manifest_version = "0.9.0"
    elif change == "stale_connection":
        connection.manifest_version = "0.9.0"
    elif change == "missing_reference":
        refs = []
    elif change == "revoked_reference":
        reference.revoked_at = datetime.now(UTC)
    elif change == "stale_reference":
        reference.manifest_version = "0.9.0"
    elif change == "connection_disabled":
        connection.enabled = False
    assert connection_status(plugin, grant, connection, manifest, "user", refs) == expected


def test_corrupted_persisted_manifest_fails_closed_without_echo(manifest):
    row = Plugin(
        id="wrong.id",
        namespace=manifest.namespace,
        version=manifest.version,
        policy_version=manifest.policy_version,
        manifest_json=manifest.canonical_data(),
    )
    with pytest.raises(PluginServiceError) as failure:
        _manifest(row)
    assert failure.value.status_code == 503
    assert "wrong.id" not in failure.value.detail


@pytest.mark.asyncio
async def test_feature_flag_off_hides_api_even_without_auth(monkeypatch):
    from backend.routes import plugins

    monkeypatch.setattr(
        plugins,
        "get_settings",
        lambda: SimpleNamespace(v2_features=SimpleNamespace(is_enabled=lambda _: False)),
    )
    application = FastAPI()
    application.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        assert (await client.get("/api/plugins")).status_code == 404
        assert (await client.get("/api/admin/plugins")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("role,status", [(None, 401), ("user", 403)])
async def test_admin_routes_enforce_auth_before_service(role, status):
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[require_plugin_feature] = lambda: None
    application.dependency_overrides[get_db_session] = lambda: None
    application.dependency_overrides[get_optional_user] = lambda: (
        User(id=uuid4(), role=role, is_active=True) if role else None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(application), base_url="http://test"
    ) as client:
        assert (await client.get("/api/admin/plugins")).status_code == status
