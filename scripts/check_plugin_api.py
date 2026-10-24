"""Exercise the plugin API with real PostgreSQL and synthetic users in the Compose dev DB."""

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.main import app
from backend.models import (
    AuditLog,
    Plugin,
    PluginCatalogState,
    PluginConnection,
    PluginCredentialReference,
    PluginManifestRelease,
    PluginPolicyRelease,
    User,
)
from scripts.auth_probe_diagnostics import auth_diagnostics

ROOT = Path(__file__).resolve().parents[1]


async def check() -> dict:
    settings = get_settings()
    if (
        settings.app_env != "development"
        or settings.db_host != "postgres"
        or not settings.v2_features.is_enabled("plugins")
    ):
        raise RuntimeError(
            "Use the Compose dev DB and enable V2/plugins only for this probe process"
        )
    suffix = uuid4().hex
    plugin_id, namespace = f"probe.{suffix}", f"p{suffix[:10]}"
    manifest = json.loads((ROOT / "plugins/manifests/demo.json").read_text())
    manifest.update(id=plugin_id, namespace=namespace)
    manifest["servers"][0]["credentials"] = [{"slot": "token", "kind": "bearer_token"}]
    emails = [f"plugin-{role}-{suffix}@example.com" for role in ("admin", "a", "b")]
    user_ids = []
    private_refs = [str(uuid4()), str(uuid4())]

    async def request(client, method, path, expected=200, body=None):
        response = await client.request(
            method, path, **({"json": body} if body is not None else {})
        )
        if response.status_code != expected:
            diagnostics = (
                await auth_diagnostics(client, response, settings)
                if (response.status_code == 401 and expected != 401)
                else {}
            )
            raise AssertionError(
                f"{method} {path}: expected {expected}, got {response.status_code}: "
                f"{response.text[:250]}; auth timing: {diagnostics}"
            )
        assert not any(secret in response.text for secret in private_refs)
        return response.json()

    try:
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://probe") as admin,
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://probe") as owner,
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://probe") as other,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://probe"
            ) as anonymous,
        ):
            for client, email in zip((admin, owner, other), emails, strict=True):
                result = await request(
                    client,
                    "POST",
                    "/api/auth/register",
                    201,
                    {
                        "email": email,
                        "password": f"Plugin@{suffix[:12]}",
                        "full_name": "Synthetic plugin probe",
                    },
                )
                user_ids.append(UUID(result["user"]["id"]))
            async with SessionFactory() as session:
                await session.execute(
                    update(User).where(User.id == user_ids[0]).values(role="admin")
                )
                await session.commit()
            await request(anonymous, "GET", "/api/plugins", 401)
            await request(anonymous, "GET", "/api/admin/plugins", 401)
            await request(owner, "GET", "/api/admin/plugins", 403)
            assert (await anonymous.get("/plugins")).status_code == 303
            assert (await owner.get("/admin/plugins")).status_code == 403
            user_page = await owner.get("/plugins")
            assert user_page.status_code == 200 and 'data-enabled="true"' in user_page.text
            assert "data-plugin-register" not in user_page.text
            assert (await admin.get("/admin/plugins")).status_code == 200
            await request(anonymous, "GET", "/api/admin/mcp-runtime", 401)
            await request(owner, "GET", "/api/admin/mcp-runtime", 403)
            runtime_health = await request(admin, "GET", "/api/admin/mcp-runtime")
            assert "servers" in runtime_health and "command" not in json.dumps(runtime_health)
            await request(anonymous, "GET", "/api/capabilities", 401)
            public_catalog = await request(owner, "GET", "/api/capabilities")
            assert public_catalog["scope"] == "host_runtime"
            assert "parameters" not in json.dumps(public_catalog)
            await request(owner, "POST", "/api/admin/mcp-runtime/missing/discover", 403)
            await request(admin, "POST", "/api/admin/mcp-runtime/missing/discover", 404)

            async def context():
                return await request(admin, "GET", "/api/admin/plugins")

            initial = await context()
            registered = await request(
                admin,
                "PUT",
                f"/api/admin/plugins/{plugin_id}",
                body={"manifest": manifest, "expected_catalog_version": initial["catalog_version"]},
            )
            assert registered["plugins"][0]["enabled"] is False
            await request(
                admin,
                "PUT",
                f"/api/admin/plugins/{plugin_id}",
                409,
                {"manifest": manifest, "expected_catalog_version": initial["catalog_version"]},
            )
            altered = {**manifest, "description": "Changed without a new version."}
            await request(
                admin,
                "PUT",
                f"/api/admin/plugins/{plugin_id}",
                409,
                {"manifest": altered, "expected_catalog_version": registered["catalog_version"]},
            )
            collision = {**manifest, "id": f"probe.other_{suffix}"}
            await request(
                admin,
                "PUT",
                f"/api/admin/plugins/{collision['id']}",
                409,
                {"manifest": collision, "expected_catalog_version": registered["catalog_version"]},
            )
            assert (await context())["auth_version"] == registered["auth_version"]
            capability = f"{namespace}.tool.main.calculate"
            skill = f"{namespace}.skill.multi_step_calculation"

            async def grant(user_id, enabled=True, ids=None):
                current = await context()
                return await request(
                    admin,
                    "PUT",
                    f"/api/admin/plugins/{plugin_id}/grants/{user_id}",
                    body={
                        "enabled": enabled,
                        "capability_ids": ids
                        if ids is not None
                        else ([capability] if enabled else []),
                        "expected_auth_version": current["auth_version"],
                    },
                )

            for ids in (["unknown.tool"], [f"{namespace}.tool.main.admin_runtime"], [skill]):
                current = await context()
                await request(
                    admin,
                    "PUT",
                    f"/api/admin/plugins/{plugin_id}/grants/{user_ids[1]}",
                    422,
                    {
                        "enabled": True,
                        "capability_ids": ids,
                        "expected_auth_version": current["auth_version"],
                    },
                )
            await request(
                owner,
                "PUT",
                f"/api/admin/plugins/{plugin_id}/grants/{user_ids[1]}",
                403,
                {
                    "enabled": True,
                    "capability_ids": [capability],
                    "expected_auth_version": (await context())["auth_version"],
                },
            )
            assert (await request(other, "GET", "/api/plugins"))["plugins"] == []
            await request(
                other,
                "POST",
                "/api/plugin-connections",
                404,
                {
                    "plugin_id": plugin_id,
                    "expected_auth_version": (await context())["auth_version"],
                },
            )
            await grant(user_ids[1])
            await grant(user_ids[2])
            connections = []
            for client in (owner, other):
                created = await request(
                    client,
                    "POST",
                    "/api/plugin-connections",
                    201,
                    {
                        "plugin_id": plugin_id,
                        "expected_auth_version": (await context())["auth_version"],
                    },
                )
                connections.append(created["connections"][0]["id"])
            a, b = connections
            await request(other, "GET", f"/api/plugin-connections/{a}", 404)
            await request(
                owner,
                "PATCH",
                f"/api/plugin-connections/{b}/enabled",
                404,
                {"enabled": False, "expected_auth_version": (await context())["auth_version"]},
            )

            async def toggle(client, connection_id, enabled, expected=200):
                return await request(
                    client,
                    "PATCH",
                    f"/api/plugin-connections/{connection_id}/enabled",
                    expected,
                    {
                        "enabled": enabled,
                        "expected_auth_version": (await context())["auth_version"],
                    },
                )

            await toggle(owner, a, True, 409)
            await request(
                admin,
                "PATCH",
                f"/api/admin/plugins/{plugin_id}/enabled",
                body={
                    "enabled": True,
                    "expected_catalog_version": (await context())["catalog_version"],
                },
            )
            await toggle(owner, a, True, 409)
            ref_body = {
                "server_id": "main",
                "slot": "token",
                "provider": "vault",
                "secret_id": private_refs[0],
            }
            await request(
                owner,
                "POST",
                f"/api/admin/plugin-connections/{a}/credential-references",
                403,
                {**ref_body, "expected_auth_version": (await context())["auth_version"]},
            )
            bound = await request(
                admin,
                "POST",
                f"/api/admin/plugin-connections/{a}/credential-references",
                201,
                {**ref_body, "expected_auth_version": (await context())["auth_version"]},
            )
            ref_a = bound["credential_references"][0]["id"]
            await request(
                admin,
                "POST",
                f"/api/admin/plugin-connections/{b}/credential-references",
                409,
                {**ref_body, "expected_auth_version": (await context())["auth_version"]},
            )
            await request(
                other,
                "POST",
                f"/api/plugin-connections/{a}/credential-references/{ref_a}/revoke",
                404,
                {"expected_auth_version": (await context())["auth_version"]},
            )
            # The composite FK must block a cross-owner binding even when API checks are bypassed.
            async with SessionFactory() as session:
                session.add(
                    PluginCredentialReference(
                        connection_id=UUID(a),
                        user_id=user_ids[2],
                        plugin_id=plugin_id,
                        server_id="main",
                        slot="forged",
                        provider="vault",
                        secret_id=uuid4(),
                        manifest_version="1.0.0",
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                else:
                    raise AssertionError("Composite credential ownership FK was bypassed")
            configured = await toggle(owner, a, True)
            assert configured["connections"][0]["effective_enabled"]
            assert not configured["connections"][0]["runtime_connected"]
            noop = await toggle(owner, a, True)
            assert noop["auth_version"] == configured["auth_version"]
            await grant(user_ids[1], False)
            revoked = await request(owner, "GET", f"/api/plugin-connections/{a}")
            assert revoked["connections"][0]["status"] == "grant_revoked"
            await toggle(owner, a, True, 409)
            await grant(user_ids[1])
            assert not (await request(owner, "GET", f"/api/plugin-connections/{a}"))["connections"][
                0
            ]["enabled"]
            await toggle(owner, a, True)
            await request(
                owner,
                "POST",
                f"/api/plugin-connections/{a}/credential-references/{ref_a}/revoke",
                body={"expected_auth_version": (await context())["auth_version"]},
            )
            await toggle(owner, a, True, 409)
            rebound = await request(
                admin,
                "POST",
                f"/api/admin/plugin-connections/{a}/credential-references",
                201,
                {
                    **ref_body,
                    "secret_id": private_refs[1],
                    "expected_auth_version": (await context())["auth_version"],
                },
            )
            ref_a = rebound["credential_references"][0]["id"]
            await toggle(owner, a, True)
            expected_auth = (await context())["auth_version"]
            races = await asyncio.gather(
                owner.patch(
                    f"/api/plugin-connections/{a}/enabled",
                    json={"enabled": False, "expected_auth_version": expected_auth},
                ),
                owner.post(
                    f"/api/plugin-connections/{a}/credential-references/{ref_a}/revoke",
                    json={"expected_auth_version": expected_auth},
                ),
            )
            assert sorted(response.status_code for response in races) == [200, 409]
            assert (await context())["auth_version"] == expected_auth + 1
            newer = {**manifest, "version": "1.0.1", "description": "Updated synthetic metadata."}
            await request(
                admin,
                "PUT",
                f"/api/admin/plugins/{plugin_id}",
                body={
                    "manifest": newer,
                    "expected_catalog_version": (await context())["catalog_version"],
                },
            )
            async with SessionFactory() as session:
                releases = (
                    await session.scalars(
                        select(PluginManifestRelease).where(
                            PluginManifestRelease.plugin_id == plugin_id
                        )
                    )
                ).all()
                policies = (
                    await session.scalars(
                        select(PluginPolicyRelease).where(
                            PluginPolicyRelease.plugin_id == plugin_id
                        )
                    )
                ).all()
                assert len(releases) == 2 and len(policies) == 1
                assert all(
                    not row.enabled
                    for row in (
                        await session.scalars(
                            select(PluginConnection).where(PluginConnection.plugin_id == plugin_id)
                        )
                    ).all()
                )
                assert all(
                    row.revoked_at
                    for row in (
                        await session.scalars(
                            select(PluginCredentialReference).where(
                                PluginCredentialReference.plugin_id == plugin_id
                            )
                        )
                    ).all()
                )
            # Returning to an old manifest must not revive grants, consent or credentials.
            await request(
                admin,
                "PUT",
                f"/api/admin/plugins/{plugin_id}",
                body={
                    "manifest": manifest,
                    "expected_catalog_version": (await context())["catalog_version"],
                },
            )
            await request(
                admin,
                "PATCH",
                f"/api/admin/plugins/{plugin_id}/enabled",
                body={
                    "enabled": True,
                    "expected_catalog_version": (await context())["catalog_version"],
                },
            )
            assert not (await request(owner, "GET", f"/api/plugin-connections/{a}"))["connections"][
                0
            ]["effective_enabled"]
            # Role changes are re-evaluated from the authenticated DB user, not the old grant/token.
            async with SessionFactory() as session:
                await session.execute(
                    update(User).where(User.id == user_ids[2]).values(role="admin")
                )
                await session.commit()
            await grant(user_ids[2], ids=[f"{namespace}.tool.main.admin_runtime"])
            async with SessionFactory() as session:
                await session.execute(
                    update(User).where(User.id == user_ids[2]).values(role="user")
                )
                await session.commit()
            assert (await request(other, "GET", "/api/plugins"))["plugins"][0][
                "capability_ids"
            ] == []
            await toggle(other, b, True, 409)
            async with SessionFactory() as session:
                logs = (
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.metadata_json["plugin_id"].astext == plugin_id
                        )
                    )
                ).all()
                assert logs and not any(
                    value in json.dumps([row.metadata_json for row in logs])
                    for value in private_refs
                )
            return {
                "admin_user_authorization": True,
                "plugin_ui_pages": True,
                "cross_owner_isolation": True,
                "credential_composite_fk": True,
                "opaque_reference_not_disclosed": True,
                "stale_cas_rejected": True,
                "concurrent_cas_one_winner": True,
                "immutable_release_history": True,
                "revocation_and_version_rollback_safe": True,
                "role_demotion_checked": True,
                "runtime_connections_started": 0,
                "llm_provider_calls": 0,
            }
    finally:
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


async def main() -> None:
    try:
        print(json.dumps(await check()))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
