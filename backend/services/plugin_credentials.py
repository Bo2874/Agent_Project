"""Owner-scoped encrypted credentials and preregistered OAuth authorization-code lifecycle."""

import base64
import hashlib
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import delete, select, update

from agent_core.mcp.outbound import outbound_client
from agent_core.plugins.namespace import tool_capability_id
from backend.core.config import get_settings
from backend.core.credential_cipher import credential_cipher
from backend.core.mcp_http_config import build_http_config, load_http_configuration, profile_policy
from backend.database import SessionFactory
from backend.models import (
    PluginCredentialReference,
    PluginGrant,
    PluginOAuthState,
    PluginSecret,
    User,
)
from backend.schemas.plugin import CredentialReferenceItem, CredentialReferencesResponse
from backend.services import plugins as control


def fingerprint(profile):
    return hashlib.sha256(profile.model_dump_json().encode()).hexdigest()


def binding(row):
    value = {
        key: str(getattr(row, key))
        for key in (
            "connection_id",
            "user_id",
            "plugin_id",
            "server_id",
            "slot",
            "manifest_version",
        )
    }
    value["record_id"] = str(getattr(row, "id", None) or row.state_hash)
    if hasattr(row, "profile_fingerprint"):
        value["profile_fingerprint"] = row.profile_fingerprint
    return value


async def authorized(session, actor, connection_id, server_id, slot):
    if not get_settings().v2_features.is_enabled("plugins"):
        raise control.PluginServiceError(404, "Plugin APIs are disabled")
    actor = await session.get(User, actor.id, populate_existing=True)
    if actor is None or not actor.is_active:
        raise control.PluginServiceError(403, "Connection owner is inactive")
    connection = await control._owned_connection(session, actor, connection_id)
    plugin = await control._plugin(session, connection.plugin_id)
    grant = await session.scalar(
        select(PluginGrant)
        .where(
            PluginGrant.user_id == actor.id,
            PluginGrant.plugin_id == plugin.id,
        )
        .execution_options(populate_existing=True)
    )
    manifest = control._manifest(plugin)
    scope = control._effective_scope(manifest, actor.role, grant.capability_ids) if grant else ()
    if (
        not actor.is_active
        or not grant
        or not grant.enabled
        or grant.manifest_version != plugin.version
    ):
        raise control.PluginServiceError(409, "A current plugin grant is required")
    server = next((server for server in manifest.servers if server.server_id == server_id), None)
    if server is None or not any(
        tool_capability_id(manifest.namespace, server_id, tool.name) in scope
        for tool in server.tools
    ):
        raise control.PluginServiceError(404, "Granted server not found")
    requirement = next((item for item in server.credentials if item.slot == slot), None)
    if requirement is None or len(server.credentials) != 1 or server.transport != "streamable_http":
        raise control.PluginServiceError(422, "A single HTTP credential slot is required")
    profile = next(
        (
            profile
            for profile in load_http_configuration(get_settings()).profiles
            if profile.id == server.connection_profile_ref
        ),
        None,
    )
    if profile is None or profile.release != server.release:
        raise control.PluginServiceError(503, "Reviewed HTTP profile is required")
    return connection, plugin, manifest, server, requirement, profile, scope


async def response(session, state, reference):
    return CredentialReferencesResponse(
        **await control._context(session, state),
        credential_references=[CredentialReferenceItem.model_validate(reference)],
    )


async def save_secret(session, state, actor, details, slot, payload, expires_at):
    connection, plugin, _, server, requirement, profile, _ = details
    await expire_states(session, actor.id, connection.id, server.server_id, slot)
    for old in await control._references(session, connection):
        if old.server_id == server.server_id and old.slot == slot:
            old.revoked_at = datetime.now(UTC)
            if old.provider == "encrypted_store":
                await session.execute(
                    update(PluginSecret)
                    .where(
                        PluginSecret.id == old.secret_id,
                        PluginSecret.user_id == actor.id,
                        PluginSecret.connection_id == connection.id,
                    )
                    .values(revoked_at=datetime.now(UTC))
                )
    secret = PluginSecret(
        id=uuid4(),
        connection_id=connection.id,
        user_id=actor.id,
        plugin_id=plugin.id,
        server_id=server.server_id,
        slot=slot,
        manifest_version=plugin.version,
        kind=requirement.kind,
        profile_fingerprint=fingerprint(profile),
        expires_at=expires_at,
    )
    secret.ciphertext = credential_cipher().seal(binding(secret), payload)
    session.add(secret)
    connection.enabled = False
    await session.flush()
    reference = PluginCredentialReference(
        connection_id=connection.id,
        user_id=actor.id,
        plugin_id=plugin.id,
        server_id=server.server_id,
        slot=slot,
        manifest_version=plugin.version,
        provider="encrypted_store",
        secret_id=secret.id,
    )
    session.add(reference)
    await session.flush()
    state.auth_version += 1
    control._audit(session, actor, "plugin.credential.store", plugin.id, reference.id)
    return reference


async def set_static(session, actor, connection_id, server_id, slot, value, expected):
    async with control._mutation(session) as state:
        control._expect_auth(state, expected)
        details = await authorized(session, actor, connection_id, server_id, slot)
        if details[4].kind not in {"api_key", "bearer_token"}:
            raise control.PluginServiceError(422, "Use OAuth authorization for this slot")
        reference = await save_secret(
            session, state, actor, details, slot, {"access_token": value}, None
        )
        result = await response(session, state, reference)
    return result


async def begin_oauth(session, actor, connection_id, server_id, slot, expected):
    async with control._mutation(session) as state:
        control._expect_auth(state, expected)
        details = await authorized(session, actor, connection_id, server_id, slot)
        connection, plugin, _, _, requirement, profile, _ = details
        if requirement.kind != "oauth2" or profile.oauth is None:
            raise control.PluginServiceError(422, "OAuth profile is required")
        oauth = profile.oauth
        await session.execute(
            delete(PluginOAuthState).where(
                PluginOAuthState.user_id == actor.id,
                PluginOAuthState.expires_at < datetime.now(UTC) - timedelta(days=1),
            )
        )
        await expire_states(session, actor.id, connection.id, server_id, slot)
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        row = PluginOAuthState(
            state_hash=hashlib.sha256(nonce.encode()).hexdigest(),
            connection_id=connection.id,
            user_id=actor.id,
            plugin_id=plugin.id,
            server_id=server_id,
            slot=slot,
            manifest_version=plugin.version,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        row.ciphertext = credential_cipher().seal(
            binding(row),
            {
                "verifier": verifier,
                "profile": fingerprint(profile),
                "consent": await grant_stamp(session, actor.id, plugin.id),
            },
        )
        session.add(row)
        control._audit(session, actor, "plugin.oauth.begin", plugin.id, connection.id)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        url = (
            oauth.authorization_endpoint
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": oauth.client_id,
                    "redirect_uri": oauth.redirect_uri,
                    "scope": " ".join(oauth.scopes),
                    "state": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": profile.endpoint,
                }
            )
        )
    return {"authorization_url": url, "expires_in": 600}


async def token_request(profile, form):
    try:
        async with outbound_client(profile_policy(profile)) as client:
            async with client.stream("POST", profile.oauth.token_endpoint, data=form) as reply:
                raw = bytearray()
                async for chunk in reply.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 64 * 1024:
                        raise ValueError("Oversized token response")
                if reply.status_code != 200:
                    raise ValueError("Token exchange rejected")
        import json

        value = json.loads(raw)
        token = value.get("access_token")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 16384
            or any(not 33 <= ord(c) <= 126 for c in token)
        ):
            raise ValueError("Invalid token")
        if value.get("token_type", "").lower() != "bearer":
            raise ValueError("Unsupported token type")
        refresh = value.get("refresh_token")
        if refresh is not None and (
            not isinstance(refresh, str)
            or not 1 <= len(refresh) <= 16384
            or any(not 33 <= ord(c) <= 126 for c in refresh)
        ):
            raise ValueError("Invalid refresh token")
        if value.get("resource", profile.endpoint) != profile.endpoint:
            raise ValueError("Resource mismatch")
        returned = value.get("scope", " ".join(profile.oauth.scopes))
        if not isinstance(returned, str) or not set(returned.split()).issubset(
            profile.oauth.scopes
        ):
            raise ValueError("Unexpected scope")
        expiry = value.get("expires_in")
        if expiry is not None and (
            isinstance(expiry, bool) or not isinstance(expiry, int) or not 1 <= expiry <= 31536000
        ):
            raise ValueError("Invalid expiry")
        return {"access_token": token, "refresh_token": refresh}, (
            datetime.now(UTC) + timedelta(seconds=expiry) if expiry is not None else None
        )
    except Exception:
        raise control.PluginServiceError(
            409, "OAuth requires reauthorization; token exchange failed"
        ) from None


async def complete_oauth(session, actor, connection_id, nonce, code, issuer):
    # Consume before network I/O: failed/exchanged codes cannot be replayed by concurrent workers.
    async with control._mutation(session):
        row = await session.scalar(
            select(PluginOAuthState)
            .where(
                PluginOAuthState.state_hash == hashlib.sha256(nonce.encode()).hexdigest(),
                PluginOAuthState.user_id == actor.id,
                PluginOAuthState.connection_id == connection_id,
            )
            .with_for_update()
        )
        if row is None or row.consumed_at or row.expires_at <= datetime.now(UTC):
            raise control.PluginServiceError(409, "OAuth state is expired or already consumed")
        details = await authorized(session, actor, connection_id, row.server_id, row.slot)
        profile = details[5]
        pending = credential_cipher().open(binding(row), row.ciphertext)
        if (
            profile.oauth is None
            or pending["profile"] != fingerprint(profile)
            or row.manifest_version != details[1].version
        ):
            raise control.PluginServiceError(409, "OAuth configuration changed; start again")
        if pending["consent"] != await grant_stamp(session, actor.id, details[1].id):
            raise control.PluginServiceError(409, "OAuth grant changed; start again")
        if (issuer is not None and issuer != profile.oauth.issuer) or (
            issuer is None and profile.oauth.require_response_issuer
        ):
            raise control.PluginServiceError(409, "OAuth issuer mismatch")
        row.consumed_at = datetime.now(UTC)
        server_id, slot, version = row.server_id, row.slot, row.manifest_version
    payload, expiry = await token_request(
        profile,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": profile.oauth.client_id,
            "redirect_uri": profile.oauth.redirect_uri,
            "code_verifier": pending["verifier"],
            "resource": profile.endpoint,
        },
    )
    async with control._mutation(session) as state:
        details = await authorized(session, actor, connection_id, server_id, slot)
        await session.refresh(row)
        if row.expires_at <= datetime.now(UTC) or pending["consent"] != await grant_stamp(
            session,
            actor.id,
            details[1].id,
        ):
            raise control.PluginServiceError(409, "OAuth consent changed; start again")
        if details[1].version != version or fingerprint(details[5]) != fingerprint(profile):
            raise control.PluginServiceError(409, "OAuth authorization changed; start again")
        reference = await save_secret(session, state, actor, details, slot, payload, expiry)
        result = await response(session, state, reference)
    return result


async def expire_states(session, user_id, connection_id, server_id, slot):
    await session.execute(
        update(PluginOAuthState)
        .where(
            PluginOAuthState.user_id == user_id,
            PluginOAuthState.connection_id == connection_id,
            PluginOAuthState.server_id == server_id,
            PluginOAuthState.slot == slot,
            PluginOAuthState.expires_at > datetime.now(UTC),
        )
        .values(expires_at=datetime.now(UTC))
    )


async def grant_stamp(session, user_id, plugin_id):
    row = (
        await session.execute(
            select(PluginGrant.id, PluginGrant.updated_at).where(
                PluginGrant.user_id == user_id,
                PluginGrant.plugin_id == plugin_id,
            )
        )
    ).one()
    return f"{row.id}:{row.updated_at.isoformat()}"


async def complete_state(session, actor, nonce, code, issuer):
    connection_id = await session.scalar(
        select(PluginOAuthState.connection_id).where(
            PluginOAuthState.state_hash == hashlib.sha256(nonce.encode()).hexdigest(),
            PluginOAuthState.user_id == actor.id,
        )
    )
    if connection_id is None:
        raise control.PluginServiceError(409, "OAuth state is expired or already consumed")
    return await complete_oauth(session, actor, connection_id, nonce, code, issuer)


async def stored_secret(session, actor, details, slot):
    connection, plugin, _, server, _, profile, _ = details
    reference = await session.scalar(
        select(PluginCredentialReference).where(
            PluginCredentialReference.connection_id == connection.id,
            PluginCredentialReference.user_id == actor.id,
            PluginCredentialReference.server_id == server.server_id,
            PluginCredentialReference.slot == slot,
            PluginCredentialReference.provider == "encrypted_store",
            PluginCredentialReference.revoked_at.is_(None),
            PluginCredentialReference.manifest_version == plugin.version,
        )
    )
    row = (
        await session.scalar(
            select(PluginSecret)
            .where(
                PluginSecret.id == reference.secret_id,
                PluginSecret.user_id == actor.id,
                PluginSecret.connection_id == connection.id,
                PluginSecret.plugin_id == plugin.id,
                PluginSecret.server_id == server.server_id,
                PluginSecret.slot == slot,
                PluginSecret.manifest_version == plugin.version,
                PluginSecret.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if reference
        else None
    )
    if row is None or row.profile_fingerprint != fingerprint(profile):
        raise control.PluginServiceError(409, "Credential is unavailable; reconnect is required")
    return row, reference, credential_cipher().open(binding(row), row.ciphertext)


async def authorization_headers(
    user_id,
    connection_id,
    server_id,
    slot,
    *,
    expected_consent=None,
    expected_role=None,
    expected_reference=None,
):
    failure = None
    async with SessionFactory() as session:
        async with control._mutation(session) as state:
            actor = await session.get(User, user_id, populate_existing=True)
            if actor is None or not actor.is_active:
                raise control.PluginServiceError(403, "Connection owner is inactive")
            details = await authorized(session, actor, connection_id, server_id, slot)
            connection, plugin, _, _, requirement, profile, _ = details
            if (expected_role is not None and actor.role != expected_role) or (
                expected_consent is not None
                and expected_consent != await grant_stamp(session, actor.id, plugin.id)
            ):
                raise control.PluginServiceError(
                    409, "Runtime authorization changed; reconnect is required"
                )
            if (
                not plugin.enabled
                or not connection.enabled
                or connection.manifest_version != plugin.version
            ):
                raise control.PluginServiceError(409, "Connection is disabled")
            row, reference, payload = await stored_secret(session, actor, details, slot)
            if expected_reference is not None and reference.id != expected_reference:
                raise control.PluginServiceError(
                    409, "Credential binding changed; reconnect is required"
                )
            if row.expires_at and row.expires_at <= datetime.now(UTC) + timedelta(seconds=30):
                refresh = payload.get("refresh_token")
                try:
                    if not refresh or profile.oauth is None:
                        raise control.PluginServiceError(
                            409, "Credential expired; reconnect is required"
                        )
                    updated, expiry = await token_request(
                        profile,
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": refresh,
                            "client_id": profile.oauth.client_id,
                            "resource": profile.endpoint,
                        },
                    )
                    updated["refresh_token"] = updated.get("refresh_token") or refresh
                    row.ciphertext = credential_cipher().seal(binding(row), updated)
                    row.expires_at, row.revision, payload = expiry, row.revision + 1, updated
                    state.auth_version += 1
                    control._audit(session, actor, "plugin.oauth.refresh", plugin.id, reference.id)
                except control.PluginServiceError as exc:
                    connection.enabled = False
                    reference.revoked_at = row.revoked_at = datetime.now(UTC)
                    state.auth_version += 1
                    failure = exc
                    control._audit(
                        session,
                        actor,
                        "plugin.oauth.reauthorization_required",
                        plugin.id,
                        reference.id,
                    )
            header = "x-api-key" if requirement.kind == "api_key" else "authorization"
            if requirement.kind == "api_key" and profile.credential_header != "x-api-key":
                raise control.PluginServiceError(409, "API key header is not configured")
            value = payload["access_token"]
            headers = {header: value if header == "x-api-key" else f"Bearer {value}"}
    if failure:
        raise failure
    return headers


async def private_http_config(session, actor, connection_id, server_id):
    connection = await control._owned_connection(session, actor, connection_id)
    plugin = await control._plugin(session, connection.plugin_id)
    manifest = control._manifest(plugin)
    server = next((s for s in manifest.servers if s.server_id == server_id), None)
    if server is None or len(server.credentials) != 1:
        raise control.PluginServiceError(422, "A single HTTP credential slot is required")
    slot = server.credentials[0].slot
    details = await authorized(session, actor, connection_id, server_id, slot)
    scope = details[6]
    scoped_server = server.model_copy(
        update={
            "tools": tuple(
                tool
                for tool in server.tools
                if tool_capability_id(manifest.namespace, server_id, tool.name) in scope
            )
        }
    )
    user_id = actor.id
    consent = await grant_stamp(session, user_id, plugin.id)
    _, reference, _ = await stored_secret(session, actor, details, slot)
    role, reference_id = actor.role, reference.id

    async def auth():
        return await authorization_headers(
            user_id,
            connection_id,
            server_id,
            slot,
            expected_consent=consent,
            expected_role=role,
            expected_reference=reference_id,
        )

    runtime = build_http_config(
        manifest, scoped_server, details[5], owner_user_id=user_id, authorization=auth
    )
    # Legacy executor memoization runs before the handler. Private HTTP must recheck authority
    # even for repeated arguments, so keep retries/memoization off until its cache is auth-aware.
    return replace(
        runtime,
        bindings=tuple(
            replace(item, policy=replace(item.policy, idempotent=False, max_retries=0))
            for item in runtime.bindings
        ),
    )


async def revoke(session, actor, connection_id, reference_id, expected):
    remote, payload = None, None
    async with control._mutation(session) as state:
        control._expect_auth(state, expected)
        connection = await control._owned_connection(session, actor, connection_id)
        reference = await session.scalar(
            select(PluginCredentialReference).where(
                PluginCredentialReference.id == reference_id,
                PluginCredentialReference.connection_id == connection.id,
                PluginCredentialReference.user_id == actor.id,
            )
        )
        if reference is None:
            raise control.PluginServiceError(404, "Credential reference not found")
        if reference.revoked_at is None:
            row = (
                await session.scalar(
                    select(PluginSecret)
                    .where(
                        PluginSecret.id == reference.secret_id,
                        PluginSecret.user_id == actor.id,
                        PluginSecret.connection_id == connection.id,
                        PluginSecret.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
                if reference.provider == "encrypted_store"
                else None
            )
            if row is not None:
                try:
                    plugin = await control._plugin(session, connection.plugin_id)
                    server = next(
                        (
                            s
                            for s in control._manifest(plugin).servers
                            if s.server_id == reference.server_id
                        ),
                        None,
                    )
                    profile = next(
                        (
                            p
                            for p in load_http_configuration(get_settings()).profiles
                            if server and p.id == server.connection_profile_ref
                        ),
                        None,
                    )
                    if (
                        profile
                        and profile.oauth
                        and profile.oauth.revocation_endpoint
                        and (row.profile_fingerprint == fingerprint(profile))
                    ):
                        payload = credential_cipher().open(binding(row), row.ciphertext)
                        remote = profile
                except Exception:
                    remote, payload = None, None
                row.revoked_at = datetime.now(UTC)
            reference.revoked_at = datetime.now(UTC)
            connection.enabled = False
            state.auth_version += 1
            control._audit(
                session, actor, "plugin.credential.revoke", connection.plugin_id, reference.id
            )
        result = (await response(session, state, reference)).model_dump(mode="json")
    # Local revocation commits even if the remote provider is unavailable.
    result["remote_revocation"] = "not_configured"
    if remote:
        try:
            async with outbound_client(profile_policy(remote)) as client:
                for key in ("refresh_token", "access_token"):
                    if payload.get(key):
                        async with client.stream(
                            "POST",
                            remote.oauth.revocation_endpoint,
                            data={
                                "token": payload[key],
                                "token_type_hint": key,
                                "client_id": remote.oauth.client_id,
                            },
                        ) as reply:
                            if reply.status_code != 200:
                                raise ValueError("Revocation rejected")
            result["remote_revocation"] = "succeeded"
        except Exception:
            result["remote_revocation"] = "failed"
    return result
