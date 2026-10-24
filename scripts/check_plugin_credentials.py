"""PostgreSQL + local TLS OAuth/credential lifecycle probe; synthetic owners and secrets only."""

import asyncio
import json
import logging
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import delete, select, update

from agent_core.mcp.outbound import outbound_client
from agent_core.plugins.manager import PluginManager
from agent_core.skills import SkillRegistry
from agent_core.tools import ToolContext, ToolExecutor, ToolRegistry
from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.main import app
from backend.models import AuditLog, Plugin, PluginCatalogState, PluginSecret, User
from backend.services import plugin_credentials as credentials
from backend.services.plugin_runtime import plugin_runtime
from backend.services.plugins import PluginServiceError
from scripts.auth_probe_diagnostics import auth_diagnostics
from scripts.http_plugin_fixture import http_fixture


async def check():
    settings = get_settings()
    assert settings.db_host == "postgres" and settings.app_env == "development"
    assert settings.v2_features.is_enabled("plugins")
    original_path, original_key = (
        settings.mcp_http_config_path,
        settings.mcp_credential_encryption_keys,
    )
    original_client = credentials.outbound_client
    suffix = uuid4().hex
    plugin_id, namespace = f"http.{suffix}", f"h{suffix[:8]}"
    emails = [f"http-{role}-{suffix}@example.com" for role in ("admin", "a", "b")]
    ids = []
    manager = None
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpx2").setLevel(logging.CRITICAL)
    try:
        async with http_fixture(require_auth=True) as fixture:
            with tempfile.TemporaryDirectory(prefix="agent-oauth-probe-") as directory:
                config_path = Path(directory) / "http.json"
                profile = {
                    "id": "fixture.oauth.http",
                    "release": "0.1.0",
                    "endpoint": fixture.base + "/mcp",
                    "oauth": {
                        "issuer": fixture.base,
                        "authorization_endpoint": fixture.base + "/authorize",
                        "token_endpoint": fixture.base + "/token",
                        "revocation_endpoint": fixture.base + "/revoke",
                        "client_id": "fixture-public-client",
                        "scopes": ["tools:read"],
                        "redirect_uri": "https://app.example.test/api/plugins/oauth/callback",
                    },
                }
                config_path.write_text(
                    json.dumps({"schema_version": "1.0", "profiles": [profile], "servers": []})
                )
                settings.mcp_http_config_path = config_path
                settings.mcp_credential_encryption_keys = SecretStr(Fernet.generate_key().decode())
                credentials.outbound_client = lambda policy: outbound_client(
                    fixture.policy("/token", "/revoke"),
                    ssl_context=fixture.ssl_context,
                )
                async with (
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app), base_url="https://app.example.test"
                    ) as admin,
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app), base_url="https://app.example.test"
                    ) as owner,
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app), base_url="https://app.example.test"
                    ) as other,
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(fixture), base_url=fixture.base
                    ) as browser,
                ):

                    async def request(client, method, path, body=None, expected=200):
                        reply = await client.request(
                            method, path, **({"json": body} if body is not None else {})
                        )
                        diagnostics = (
                            await auth_diagnostics(client, reply, settings)
                            if (reply.status_code == 401 and expected != 401)
                            else {}
                        )
                        assert reply.status_code == expected, (
                            f"HTTP fixture {method} expected {expected}, got {reply.status_code}; "
                            f"{diagnostics}"
                        )
                        assert not any(
                            value in reply.text for value in fixture.access | fixture.refresh
                        )
                        return reply.json()

                    for client, email in zip((admin, owner, other), emails, strict=True):
                        result = await request(
                            client,
                            "POST",
                            "/api/auth/register",
                            {
                                "email": email,
                                "password": f"Fixture@{suffix[:12]}",
                                "full_name": "Synthetic OAuth probe",
                            },
                            201,
                        )
                        ids.append(UUID(result["user"]["id"]))
                    async with SessionFactory() as session:
                        await session.execute(
                            update(User).where(User.id == ids[0]).values(role="admin")
                        )
                        await session.commit()

                    async def context():
                        return await request(admin, "GET", "/api/admin/plugins")

                    manifest = {
                        "schema_version": "1.0",
                        "id": plugin_id,
                        "namespace": namespace,
                        "version": "1.0.0",
                        "policy_version": "1.0.0",
                        "name": "HTTP fixture",
                        "description": "Offline TLS fixture",
                        "servers": [
                            {
                                "server_id": "main",
                                "transport": "streamable_http",
                                "release": "0.1.0",
                                "connection_profile_ref": profile["id"],
                                "credentials": [{"slot": "token", "kind": "oauth2"}],
                                "tools": [
                                    {
                                        "name": "echo",
                                        "remote_name": "echo",
                                        "description": "Echo fixture",
                                        "policy": {
                                            "allowed_roles": ["user", "admin"],
                                            "side_effect": "read_only",
                                            "requires_approval": False,
                                            "idempotent": True,
                                            "max_retries": 1,
                                        },
                                    }
                                ],
                            }
                        ],
                    }

                    async def publish():
                        await request(
                            admin,
                            "PUT",
                            f"/api/admin/plugins/{plugin_id}",
                            {
                                "manifest": manifest,
                                "expected_catalog_version": (await context())["catalog_version"],
                            },
                        )
                        await request(
                            admin,
                            "PATCH",
                            f"/api/admin/plugins/{plugin_id}/enabled",
                            {
                                "enabled": True,
                                "expected_catalog_version": (await context())["catalog_version"],
                            },
                        )

                    async def grant(user_id, enabled=True):
                        return await request(
                            admin,
                            "PUT",
                            f"/api/admin/plugins/{plugin_id}/grants/{user_id}",
                            {
                                "enabled": enabled,
                                "capability_ids": [f"{namespace}.tool.main.echo"],
                                "expected_auth_version": (await context())["auth_version"],
                            },
                        )

                    await publish()
                    await grant(ids[1])
                    await grant(ids[2])
                    result = await request(
                        owner,
                        "POST",
                        "/api/plugin-connections",
                        {
                            "plugin_id": plugin_id,
                            "expected_auth_version": (await context())["auth_version"],
                        },
                        201,
                    )
                    connection_id = result["connections"][0]["id"]
                    base = f"/api/plugin-connections/{connection_id}"

                    async def begin(client=owner):
                        result = await request(
                            client,
                            "POST",
                            base + "/oauth/begin",
                            {
                                "server_id": "main",
                                "slot": "token",
                                "expected_auth_version": (await context())["auth_version"],
                            },
                        )
                        query = parse_qs(urlsplit(result["authorization_url"]).query)
                        assert query["resource"] == [profile["endpoint"]] and query[
                            "code_challenge_method"
                        ] == ["S256"]
                        approval = await browser.get(result["authorization_url"])
                        assert approval.status_code == 302
                        params = parse_qs(urlsplit(approval.headers["location"]).query)
                        return {
                            "state": params["state"][0],
                            "code": params["code"][0],
                            "issuer": params["iss"][0],
                        }

                    await request(
                        other,
                        "POST",
                        base + "/credentials",
                        {
                            "server_id": "main",
                            "slot": "token",
                            "value": "forged",
                            "expected_auth_version": (await context())["auth_version"],
                        },
                        404,
                    )
                    flow = await begin()
                    await request(
                        owner,
                        "POST",
                        base + "/oauth/complete",
                        {**flow, "issuer": "https://wrong.example"},
                        409,
                    )
                    await request(other, "POST", base + "/oauth/complete", flow, 409)
                    await request(other, "POST", "/api/plugins/oauth/complete", flow, 409)
                    assert fixture.stats["exchanges"] == 0
                    replies = await asyncio.gather(
                        *(owner.post("/api/plugins/oauth/complete", json=flow) for _ in range(2))
                    )
                    assert sorted(reply.status_code for reply in replies) == [200, 409]
                    assert fixture.stats["exchanges"] == 1
                    result = next(reply.json() for reply in replies if reply.status_code == 200)
                    reference_id = result["credential_references"][0]["id"]
                    await request(owner, "POST", base + "/oauth/complete", flow, 409)
                    async with SessionFactory() as session:
                        row = await session.scalar(
                            select(PluginSecret).where(PluginSecret.user_id == ids[1])
                        )
                        assert row and all(
                            value.encode() not in row.ciphertext
                            for value in fixture.access | fixture.refresh
                        )
                        actor = await session.get(User, ids[1])
                        runtime = await credentials.private_http_config(
                            session, actor, UUID(connection_id), "main"
                        )
                    runtime = replace(
                        runtime,
                        network_policy=fixture.policy("/mcp"),
                        ssl_context=fixture.ssl_context,
                    )
                    assert not runtime.bindings[0].policy.idempotent
                    executor = ToolExecutor()

                    async def enable():
                        await request(
                            owner,
                            "PATCH",
                            base + "/enabled",
                            {
                                "enabled": True,
                                "expected_auth_version": (await context())["auth_version"],
                            },
                        )

                    await enable()
                    registry = ToolRegistry()
                    manager = PluginManager(registry, (runtime,))
                    (health,) = await manager.start()
                    assert health.status == "healthy"
                    tool_context = ToolContext(
                        user_id=ids[1], conversation_id=uuid4(), user_role="user"
                    )

                    async def execute(actor=tool_context):
                        return await executor.execute(
                            registry,
                            actor,
                            f"{namespace}__main__echo",
                            {"value": "credential-fixture"},
                        )

                    assert not (await execute()).is_error
                    before = fixture.stats["requests"]
                    denied = await execute(replace(tool_context, user_id=ids[2]))
                    assert (
                        denied.error_code == "tool_permission_denied"
                        and fixture.stats["requests"] == before
                    )
                    async with SessionFactory() as session:
                        await session.execute(
                            update(PluginSecret)
                            .where(PluginSecret.user_id == ids[1])
                            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
                        )
                        await session.commit()
                    refreshed = await asyncio.gather(
                        *(
                            credentials.authorization_headers(
                                ids[1], UUID(connection_id), "main", "token"
                            )
                            for _ in range(2)
                        )
                    )
                    assert refreshed[0] == refreshed[1] and fixture.stats["refreshes"] == 1
                    assert not (await execute()).is_error
                    stale_consent = await begin()
                    await grant(ids[1], False)
                    try:
                        await credentials.authorization_headers(
                            ids[1], UUID(connection_id), "main", "token"
                        )
                    except PluginServiceError:
                        pass
                    else:
                        raise AssertionError("Revoked grant still resolved a token")
                    before = fixture.stats["requests"]
                    assert (await execute()).is_error and fixture.stats["requests"] == before
                    await manager.close()
                    manager = None
                    await grant(ids[1])
                    await enable()
                    try:
                        await runtime.authorization()
                    except PluginServiceError:
                        pass
                    else:
                        raise AssertionError("Old runtime survived a grant generation change")
                    await request(owner, "POST", base + "/oauth/complete", stale_consent, 409)
                    obsolete = await begin()
                    valid = await begin()
                    await request(owner, "POST", base + "/oauth/complete", obsolete, 409)
                    callback = "/api/plugins/oauth/callback?" + urlencode(
                        {
                            "code": valid["code"],
                            "state": valid["state"],
                            "iss": valid["issuer"],
                        }
                    )
                    result = await request(owner, "GET", callback)
                    reference_id = result["credential_references"][0]["id"]
                    await enable()
                    fixture.refresh.clear()
                    async with SessionFactory() as session:
                        await session.execute(
                            update(PluginSecret)
                            .where(
                                PluginSecret.user_id == ids[1],
                                PluginSecret.revoked_at.is_(None),
                            )
                            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
                        )
                        await session.commit()
                    try:
                        await credentials.authorization_headers(
                            ids[1], UUID(connection_id), "main", "token"
                        )
                    except PluginServiceError:
                        pass
                    else:
                        raise AssertionError("Invalid refresh grant did not require reconnection")
                    assert not (await request(owner, "GET", base))["connections"][0]["enabled"]
                    recovered = await begin()
                    result = await request(owner, "POST", base + "/oauth/complete", recovered)
                    reference_id = result["credential_references"][0]["id"]
                    revoked = await request(
                        owner,
                        "POST",
                        base + f"/credentials/{reference_id}/revoke",
                        {
                            "expected_auth_version": (await context())["auth_version"],
                        },
                    )
                    assert (
                        revoked["remote_revocation"] == "succeeded"
                        and fixture.stats["revocations"] == 2
                    )
                    manifest.update(version="1.0.1", policy_version="1.0.1")
                    manifest["servers"][0]["credentials"][0]["kind"] = "bearer_token"
                    await publish()
                    await grant(ids[1])
                    for token in ("static-fixture-one", "static-fixture-two"):
                        fixture.access.add(token)
                        result = await request(
                            owner,
                            "POST",
                            base + "/credentials",
                            {
                                "server_id": "main",
                                "slot": "token",
                                "value": token,
                                "expected_auth_version": (await context())["auth_version"],
                            },
                        )
                        reference_id = result["credential_references"][0]["id"]
                    await enable()
                    assert (
                        await credentials.authorization_headers(
                            ids[1], UUID(connection_id), "main", "token"
                        )
                    )["authorization"] == "Bearer static-fixture-two"
                    # Only the test fixture can dial loopback for this run-local HTTP connection.
                    original_factory = credentials.private_http_config

                    async def fixture_factory(*args, **kwargs):
                        runtime_config = await original_factory(*args, **kwargs)
                        return replace(
                            runtime_config,
                            network_policy=fixture.policy("/mcp"),
                            ssl_context=fixture.ssl_context,
                        )

                    credentials.private_http_config = fixture_factory
                    try:
                        private_alias = f"{namespace}__main__echo"
                        async with plugin_runtime(
                            ToolRegistry(allowlist={private_alias}),
                            SkillRegistry(),
                            ToolContext(ids[1], uuid4(), "user", uuid4()),
                        ) as private_run:
                            result = await ToolExecutor().execute(
                                private_run.registry,
                                private_run.context,
                                private_alias,
                                {"value": "private bridge"},
                            )
                            assert not result.is_error
                            reviewed = config_path.read_text(encoding="utf-8")
                            changed = json.loads(reviewed)
                            changed["profiles"][0]["protocol_mode"] = "legacy"
                            config_path.write_text(json.dumps(changed), encoding="utf-8")
                            before_requests = fixture.stats["requests"]
                            invalidated = await ToolExecutor().execute(
                                private_run.registry,
                                private_run.context,
                                private_alias,
                                {"value": "profile changed"},
                            )
                            assert invalidated.error_code == "plugin_profile_changed"
                            assert fixture.stats["requests"] == before_requests
                            config_path.write_text(reviewed, encoding="utf-8")
                            await grant(ids[1], False)
                            denied = await ToolExecutor().execute(
                                private_run.registry,
                                private_run.context,
                                private_alias,
                                {"value": "private bridge"},
                            )
                            assert denied.error_code == "plugin_authorization_changed"
                    finally:
                        credentials.private_http_config = original_factory
                    settings.mcp_credential_encryption_keys = None
                    local = await request(
                        owner,
                        "POST",
                        base + f"/credentials/{reference_id}/revoke",
                        {
                            "expected_auth_version": (await context())["auth_version"],
                        },
                    )
                    assert local["credential_references"][0]["revoked_at"]
                    return {
                        "encrypted_at_rest": True,
                        "pkce_resource_and_issuer": True,
                        "state_single_use_concurrent": True,
                        "browser_callback_completion": True,
                        "stale_consent_rejected": True,
                        "refresh_single_winner_rotation": True,
                        "invalid_grant_disables_connection": True,
                        "runtime_grant_generation_pinned": True,
                        "private_result_cache_disabled": True,
                        "private_http_run_bridge": True,
                        "host_profile_invalidation": True,
                        "owner_isolation": True,
                        "grant_revoke_stops_http": True,
                        "provider_revoke": True,
                        "static_rotation": True,
                        "local_revoke_without_key": True,
                        "llm_provider_calls": 0,
                    }
    finally:
        if manager:
            await manager.close()
        credentials.outbound_client = original_client
        settings.mcp_http_config_path, settings.mcp_credential_encryption_keys = (
            original_path,
            original_key,
        )
        async with SessionFactory() as session:
            state = await session.scalar(
                select(PluginCatalogState).where(PluginCatalogState.id == 1).with_for_update()
            )
            await session.execute(
                delete(AuditLog).where(AuditLog.metadata_json["plugin_id"].astext == plugin_id)
            )
            await session.execute(delete(Plugin).where(Plugin.id == plugin_id))
            await session.execute(delete(User).where(User.email.in_(emails)))
            state.revision += 1
            state.auth_version += 1
            await session.commit()


async def main():
    try:
        print(json.dumps(await check()))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
