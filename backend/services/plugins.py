"""Transactional plugin control plane. Does not start transports or resolve secret values."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.plugins import CatalogEntry, CatalogSnapshot, PluginManifest
from agent_core.plugins.manifest import MAX_MANIFEST_BYTES
from agent_core.plugins.namespace import tool_capability_id
from backend.models import (
    AuditLog,
    Plugin,
    PluginCatalogState,
    PluginConnection,
    PluginCredentialReference,
    PluginGrant,
    PluginManifestRelease,
    PluginPolicyRelease,
    User,
)
from backend.schemas.plugin import (
    AdminPluginsResponse,
    ConnectionItem,
    ConnectionsResponse,
    CredentialReferenceItem,
    CredentialReferencesResponse,
    GrantItem,
    GrantsResponse,
    PluginAdminItem,
    UserPluginItem,
    UserPluginsResponse,
)


class PluginServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _admin(actor: User) -> None:
    if actor.role != "admin" or not actor.is_active:
        raise PluginServiceError(403, "Admin access required")


async def _state(session: AsyncSession, *, write: bool = False) -> PluginCatalogState:
    state = await session.scalar(
        select(PluginCatalogState)
        .where(PluginCatalogState.id == 1)
        .with_for_update(read=not write)
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise PluginServiceError(503, "Plugin migration is required")
    return state


@asynccontextmanager
async def _mutation(session: AsyncSession):
    try:
        yield await _state(session, write=True)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise PluginServiceError(
            409, "Plugin metadata conflicts with an existing binding"
        ) from None
    except Exception:
        await session.rollback()
        raise


def _manifest(row: Plugin) -> PluginManifest:
    try:
        item = PluginManifest.model_validate(row.manifest_json)
        if (item.id, item.namespace, item.version, item.policy_version) != (
            row.id,
            row.namespace,
            row.version,
            row.policy_version,
        ):
            raise ValueError("Identity mismatch")
        return item
    except ValueError:
        raise PluginServiceError(503, "Plugin metadata is unavailable") from None


async def _plugin(session: AsyncSession, plugin_id: str) -> Plugin:
    row = await session.get(Plugin, plugin_id, populate_existing=True)
    if row is None:
        raise PluginServiceError(404, "Plugin not found")
    return row


async def _snapshot(session: AsyncSession, state: PluginCatalogState) -> CatalogSnapshot:
    rows = (
        await session.scalars(
            select(Plugin).order_by(Plugin.id).execution_options(populate_existing=True)
        )
    ).all()
    return CatalogSnapshot.build(
        tuple(CatalogEntry(manifest=_manifest(row), enabled=row.enabled) for row in rows),
        state.revision,
    )


async def _context(session: AsyncSession, state: PluginCatalogState) -> dict:
    return {
        "catalog_version": (await _snapshot(session, state)).catalog_version,
        "auth_version": state.auth_version,
    }


async def _expect_catalog(session: AsyncSession, state: PluginCatalogState, expected: str) -> None:
    if (await _snapshot(session, state)).catalog_version != expected:
        raise PluginServiceError(409, "Catalog changed; refresh before updating")


def _expect_auth(state: PluginCatalogState, expected: int) -> None:
    if state.auth_version != expected:
        raise PluginServiceError(409, "Authorization metadata changed; refresh before updating")


def _audit(
    session: AsyncSession, actor: User, action: str, plugin_id: str, target_id: UUID | None = None
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor.id,
            action=action,
            target_type="plugin",
            target_id=str(target_id) if target_id else None,
            outcome="success",
            metadata_json={"plugin_id": plugin_id},
        )
    )


def grant_scope(manifest: PluginManifest, role: str, ids: tuple[str, ...]) -> tuple[str, ...]:
    """Grant only declared, role-compatible capabilities, including explicit Skill dependencies."""
    descriptors = {cap.capability_id: cap for cap in manifest.capabilities()}
    roles = {
        tool_capability_id(
            manifest.namespace, server.server_id, tool.name
        ): tool.policy.allowed_roles
        for server in manifest.servers
        for tool in server.tools
    }
    roles.update(
        {
            f"{manifest.namespace}.skill.{skill.name}": skill.policy.allowed_roles
            for skill in manifest.skills
        }
    )
    if len(set(ids)) != len(ids) or any(
        cap not in descriptors or role not in roles.get(cap, ()) for cap in ids
    ):
        raise PluginServiceError(
            422, "Grant must use declared capabilities allowed for the target role"
        )
    if any(set(descriptors[cap].required_capability_ids) - set(ids) for cap in ids):
        raise PluginServiceError(422, "A Skill grant must include its required tools")
    return tuple(sorted(ids))


def _effective_scope(manifest: PluginManifest, role: str, ids: list[str]) -> tuple[str, ...]:
    descriptors = {cap.capability_id: cap for cap in manifest.capabilities()}
    roles = {
        tool_capability_id(
            manifest.namespace, server.server_id, tool.name
        ): tool.policy.allowed_roles
        for server in manifest.servers
        for tool in server.tools
    }
    roles.update(
        {
            f"{manifest.namespace}.skill.{skill.name}": skill.policy.allowed_roles
            for skill in manifest.skills
        }
    )
    permitted = {cap for cap in ids if cap in descriptors and role in roles[cap]}
    return tuple(
        sorted(
            cap
            for cap in permitted
            if set(descriptors[cap].required_capability_ids).issubset(permitted)
        )
    )


async def admin_plugins(
    session: AsyncSession, actor: User, *, limit: int = 50, offset: int = 0
) -> AdminPluginsResponse:
    _admin(actor)
    state = await _state(session)
    snapshot = await _snapshot(session, state)
    return AdminPluginsResponse(
        catalog_version=snapshot.catalog_version,
        auth_version=state.auth_version,
        plugins=[
            PluginAdminItem(manifest=entry.manifest, enabled=entry.enabled)
            for entry in snapshot.entries[offset : offset + limit]
        ],
    )


async def register_plugin(
    session: AsyncSession, actor: User, manifest: PluginManifest, expected: str
) -> AdminPluginsResponse:
    _admin(actor)
    manifest = PluginManifest.model_validate(manifest)
    if len(manifest.model_dump_json().encode()) > MAX_MANIFEST_BYTES:
        raise PluginServiceError(422, "Manifest exceeds the storage size limit")
    async with _mutation(session) as state:
        await _expect_catalog(session, state, expected)
        row = await session.get(Plugin, manifest.id, populate_existing=True)
        if row is not None and row.namespace != manifest.namespace:
            raise PluginServiceError(409, "Plugin namespace cannot be renamed")
        collision = await session.scalar(
            select(Plugin.id).where(
                Plugin.namespace == manifest.namespace, Plugin.id != manifest.id
            )
        )
        if collision:
            raise PluginServiceError(409, "Plugin namespace is already registered")
        release = await session.get(PluginManifestRelease, (manifest.id, manifest.version))
        policy = await session.get(PluginPolicyRelease, (manifest.id, manifest.policy_version))
        if release is not None and release.fingerprint != manifest.fingerprint():
            raise PluginServiceError(409, "Manifest release is immutable; use a new version")
        if policy is not None and policy.fingerprint != manifest.policy_fingerprint():
            raise PluginServiceError(409, "Policy release is immutable; use a new policy_version")
        if row is None or _manifest(row).fingerprint() != manifest.fingerprint():
            if row is None:
                row = Plugin(
                    id=manifest.id,
                    namespace=manifest.namespace,
                    version=manifest.version,
                    policy_version=manifest.policy_version,
                    manifest_json=manifest.canonical_data(),
                    enabled=False,
                )
                session.add(row)
                await session.flush()
            else:
                row.version, row.policy_version = manifest.version, manifest.policy_version
                row.manifest_json, row.enabled = manifest.canonical_data(), False
                # A release change requires new grants, consent and credential bindings.
                # Returning to an old version must not revive previous authorizations.
                await session.execute(
                    update(PluginGrant).where(PluginGrant.plugin_id == row.id).values(enabled=False)
                )
                await session.execute(
                    update(PluginConnection)
                    .where(PluginConnection.plugin_id == row.id)
                    .values(enabled=False)
                )
                await session.execute(
                    update(PluginCredentialReference)
                    .where(
                        PluginCredentialReference.plugin_id == row.id,
                        PluginCredentialReference.revoked_at.is_(None),
                    )
                    .values(revoked_at=datetime.now(UTC))
                )
            if release is None:
                session.add(
                    PluginManifestRelease(
                        plugin_id=manifest.id,
                        version=manifest.version,
                        fingerprint=manifest.fingerprint(),
                        manifest_json=manifest.canonical_data(),
                    )
                )
            if policy is None:
                session.add(
                    PluginPolicyRelease(
                        plugin_id=manifest.id,
                        version=manifest.policy_version,
                        fingerprint=manifest.policy_fingerprint(),
                    )
                )
            state.revision += 1
            state.auth_version += 1
            _audit(session, actor, "plugin.register", manifest.id)
        result = AdminPluginsResponse(
            **await _context(session, state),
            plugins=[PluginAdminItem(manifest=manifest, enabled=row.enabled)],
        )
    return result


async def set_plugin_enabled(
    session: AsyncSession, actor: User, plugin_id: str, enabled: bool, expected: str
) -> AdminPluginsResponse:
    _admin(actor)
    async with _mutation(session) as state:
        await _expect_catalog(session, state, expected)
        row = await _plugin(session, plugin_id)
        if row.enabled != enabled:
            row.enabled = enabled
            state.revision += 1
            state.auth_version += 1
            _audit(session, actor, "plugin.enable" if enabled else "plugin.disable", plugin_id)
        result = AdminPluginsResponse(
            **await _context(session, state),
            plugins=[PluginAdminItem(manifest=_manifest(row), enabled=row.enabled)],
        )
    return result


async def list_grants(
    session: AsyncSession, actor: User, plugin_id: str, *, limit: int = 50, offset: int = 0
) -> GrantsResponse:
    _admin(actor)
    state = await _state(session)
    await _plugin(session, plugin_id)
    rows = (
        await session.scalars(
            select(PluginGrant)
            .where(PluginGrant.plugin_id == plugin_id)
            .order_by(PluginGrant.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return GrantsResponse(
        **await _context(session, state), grants=[GrantItem.model_validate(row) for row in rows]
    )


async def set_grant(
    session: AsyncSession,
    actor: User,
    plugin_id: str,
    user_id: UUID,
    enabled: bool,
    ids: tuple[str, ...],
    expected: int,
) -> GrantsResponse:
    _admin(actor)
    async with _mutation(session) as state:
        _expect_auth(state, expected)
        plugin = await _plugin(session, plugin_id)
        user = await session.get(User, user_id, populate_existing=True)
        if user is None or not user.is_active:
            raise PluginServiceError(404, "User not found")
        if enabled and not ids:
            raise PluginServiceError(422, "Enabled grants require an explicit capability scope")
        scope = grant_scope(_manifest(plugin), user.role, ids)
        grant = await session.scalar(
            select(PluginGrant).where(
                PluginGrant.user_id == user_id, PluginGrant.plugin_id == plugin_id
            )
        )
        changed = grant is None or (
            grant.enabled,
            grant.manifest_version,
            tuple(grant.capability_ids),
        ) != (enabled, plugin.version, scope)
        if changed:
            if grant is None:
                grant = PluginGrant(
                    user_id=user_id,
                    plugin_id=plugin_id,
                    enabled=enabled,
                    manifest_version=plugin.version,
                    capability_ids=list(scope),
                    granted_by_user_id=actor.id,
                )
                session.add(grant)
            else:
                grant.enabled, grant.manifest_version = enabled, plugin.version
                grant.capability_ids, grant.granted_by_user_id = list(scope), actor.id
            await session.execute(
                update(PluginConnection)
                .where(PluginConnection.user_id == user_id, PluginConnection.plugin_id == plugin_id)
                .values(enabled=False)
            )
            state.auth_version += 1
            _audit(session, actor, "plugin.grant" if enabled else "plugin.revoke", plugin_id)
        await session.flush()
        result = GrantsResponse(
            **await _context(session, state), grants=[GrantItem.model_validate(grant)]
        )
    return result


async def user_plugins(
    session: AsyncSession, actor: User, *, limit: int = 50, offset: int = 0
) -> UserPluginsResponse:
    state = await _state(session)
    pairs = (
        await session.execute(
            select(Plugin, PluginGrant)
            .join(PluginGrant, Plugin.id == PluginGrant.plugin_id)
            .where(PluginGrant.user_id == actor.id)
            .order_by(Plugin.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    result = []
    for plugin, grant in pairs:
        manifest = _manifest(plugin)
        scope = (
            _effective_scope(manifest, actor.role, grant.capability_ids)
            if grant.enabled and grant.manifest_version == plugin.version
            else ()
        )
        result.append(
            UserPluginItem(
                id=plugin.id,
                namespace=plugin.namespace,
                name=manifest.name,
                description=manifest.description,
                version=plugin.version,
                plugin_enabled=plugin.enabled,
                grant_enabled=grant.enabled,
                grant_manifest_version=grant.manifest_version,
                capability_ids=scope,
                credential_requirements=[
                    {
                        "server_id": server.server_id,
                        "slot": requirement.slot,
                        "kind": requirement.kind,
                        "transport": server.transport,
                    }
                    for server in manifest.servers
                    for requirement in server.credentials
                ],
            )
        )
    return UserPluginsResponse(**await _context(session, state), plugins=result)


async def _owned_connection(
    session: AsyncSession, actor: User, connection_id: UUID
) -> PluginConnection:
    row = await session.scalar(
        select(PluginConnection)
        .where(PluginConnection.id == connection_id, PluginConnection.user_id == actor.id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise PluginServiceError(404, "Connection not found")
    return row


async def _references(
    session: AsyncSession, connection: PluginConnection
) -> list[PluginCredentialReference]:
    return list(
        (
            await session.scalars(
                select(PluginCredentialReference)
                .where(
                    PluginCredentialReference.connection_id == connection.id,
                    PluginCredentialReference.user_id == connection.user_id,
                    PluginCredentialReference.plugin_id == connection.plugin_id,
                    PluginCredentialReference.revoked_at.is_(None),
                )
                .order_by(PluginCredentialReference.id)
            )
        ).all()
    )


def connection_status(
    plugin: Plugin,
    grant: PluginGrant | None,
    connection: PluginConnection,
    manifest: PluginManifest,
    role: str,
    refs: list[PluginCredentialReference],
) -> str:
    if not plugin.enabled:
        return "plugin_disabled"
    if (
        grant is None
        or not grant.enabled
        or not _effective_scope(manifest, role, grant.capability_ids)
    ):
        return "grant_revoked"
    if grant.manifest_version != plugin.version or connection.manifest_version != plugin.version:
        return "version_mismatch"
    required = {
        (server.server_id, credential.slot)
        for server in manifest.servers
        for credential in server.credentials
    }
    provided = {
        (ref.server_id, ref.slot)
        for ref in refs
        if ref.manifest_version == plugin.version and ref.revoked_at is None
    }
    if not required.issubset(provided):
        return "credentials_missing"
    if not connection.enabled:
        return "connection_disabled"
    return "configured"


async def _connection_item(
    session: AsyncSession, actor: User, connection: PluginConnection
) -> ConnectionItem:
    plugin = await _plugin(session, connection.plugin_id)
    grant = await session.scalar(
        select(PluginGrant).where(
            PluginGrant.user_id == actor.id, PluginGrant.plugin_id == plugin.id
        )
    )
    refs = await _references(session, connection)
    status = connection_status(plugin, grant, connection, _manifest(plugin), actor.role, refs)
    return ConnectionItem(
        id=connection.id,
        plugin_id=plugin.id,
        enabled=connection.enabled,
        manifest_version=connection.manifest_version,
        effective_enabled=status == "configured",
        status=status,
        credential_references=[CredentialReferenceItem.model_validate(ref) for ref in refs],
    )


async def list_connections(
    session: AsyncSession,
    actor: User,
    *,
    connection_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> ConnectionsResponse:
    state = await _state(session)
    if connection_id is not None:
        rows = [await _owned_connection(session, actor, connection_id)]
    else:
        rows = (
            await session.scalars(
                select(PluginConnection)
                .where(PluginConnection.user_id == actor.id)
                .order_by(PluginConnection.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    return ConnectionsResponse(
        **await _context(session, state),
        connections=[await _connection_item(session, actor, row) for row in rows],
    )


async def create_connection(
    session: AsyncSession, actor: User, plugin_id: str, expected: int
) -> ConnectionsResponse:
    async with _mutation(session) as state:
        _expect_auth(state, expected)
        # Invisible/ungranted plugin ids have the same response as missing ids.
        grant = await session.scalar(
            select(PluginGrant).where(
                PluginGrant.user_id == actor.id, PluginGrant.plugin_id == plugin_id
            )
        )
        if grant is None or not grant.enabled:
            raise PluginServiceError(404, "Plugin grant not found")
        plugin = await _plugin(session, plugin_id)
        if grant.manifest_version != plugin.version or not _effective_scope(
            _manifest(plugin), actor.role, grant.capability_ids
        ):
            raise PluginServiceError(409, "A current plugin grant is required")
        existing = await session.scalar(
            select(PluginConnection).where(
                PluginConnection.user_id == actor.id, PluginConnection.plugin_id == plugin_id
            )
        )
        if existing is not None:
            raise PluginServiceError(409, "A connection already exists for this plugin")
        row = PluginConnection(
            user_id=actor.id, plugin_id=plugin_id, enabled=False, manifest_version=plugin.version
        )
        session.add(row)
        await session.flush()
        state.auth_version += 1
        _audit(session, actor, "plugin.connection.create", plugin_id, row.id)
        result = ConnectionsResponse(
            **await _context(session, state),
            connections=[await _connection_item(session, actor, row)],
        )
    return result


async def set_connection_enabled(
    session: AsyncSession, actor: User, connection_id: UUID, enabled: bool, expected: int
) -> ConnectionsResponse:
    async with _mutation(session) as state:
        _expect_auth(state, expected)
        row = await _owned_connection(session, actor, connection_id)
        previous_version = row.manifest_version
        if enabled:
            plugin = await _plugin(session, row.plugin_id)
            grant = await session.scalar(
                select(PluginGrant).where(
                    PluginGrant.user_id == actor.id, PluginGrant.plugin_id == plugin.id
                )
            )
            # Evaluate at the proposed version, without publishing partially on a failed check.
            proposed = PluginConnection(
                id=row.id,
                user_id=row.user_id,
                plugin_id=row.plugin_id,
                enabled=True,
                manifest_version=plugin.version,
            )
            reason = connection_status(
                plugin,
                grant,
                proposed,
                _manifest(plugin),
                actor.role,
                await _references(session, row),
            )
            if reason != "configured":
                raise PluginServiceError(409, f"Connection cannot be enabled: {reason}")
            row.manifest_version = plugin.version
        if row.enabled != enabled or row.manifest_version != previous_version:
            row.enabled = enabled
            state.auth_version += 1
            _audit(
                session,
                actor,
                "plugin.connection.enable" if enabled else "plugin.connection.disable",
                row.plugin_id,
                row.id,
            )
        result = ConnectionsResponse(
            **await _context(session, state),
            connections=[await _connection_item(session, actor, row)],
        )
    return result


async def provision_reference(
    session: AsyncSession,
    actor: User,
    connection_id: UUID,
    server_id: str,
    slot: str,
    provider: str,
    secret_id: UUID,
    expected: int,
) -> CredentialReferencesResponse:
    _admin(actor)
    async with _mutation(session) as state:
        _expect_auth(state, expected)
        connection = await session.get(PluginConnection, connection_id, populate_existing=True)
        if connection is None:
            raise PluginServiceError(404, "Connection not found")
        plugin = await _plugin(session, connection.plugin_id)
        required = {
            (server.server_id, item.slot)
            for server in _manifest(plugin).servers
            for item in server.credentials
        }
        if (server_id, slot) not in required:
            raise PluginServiceError(422, "Credential slot is not declared by the current manifest")
        existing_secret = await session.scalar(
            select(PluginCredentialReference.id).where(
                PluginCredentialReference.provider == provider,
                PluginCredentialReference.secret_id == secret_id,
            )
        )
        if existing_secret is not None:
            raise PluginServiceError(409, "Credential reference is already bound")
        for old in await _references(session, connection):
            if (old.server_id, old.slot) == (server_id, slot):
                old.revoked_at = datetime.now(UTC)
        connection.enabled = False
        await session.flush()
        row = PluginCredentialReference(
            connection_id=connection.id,
            user_id=connection.user_id,
            plugin_id=connection.plugin_id,
            server_id=server_id,
            slot=slot,
            provider=provider,
            secret_id=secret_id,
            manifest_version=plugin.version,
        )
        session.add(row)
        await session.flush()
        state.auth_version += 1
        _audit(session, actor, "plugin.credential.bind", connection.plugin_id, row.id)
        result = CredentialReferencesResponse(
            **await _context(session, state),
            credential_references=[CredentialReferenceItem.model_validate(row)],
        )
    return result


async def revoke_reference(
    session: AsyncSession, actor: User, connection_id: UUID, reference_id: UUID, expected: int
) -> CredentialReferencesResponse:
    async with _mutation(session) as state:
        _expect_auth(state, expected)
        connection = await _owned_connection(session, actor, connection_id)
        row = await session.scalar(
            select(PluginCredentialReference).where(
                PluginCredentialReference.id == reference_id,
                PluginCredentialReference.connection_id == connection.id,
                PluginCredentialReference.user_id == actor.id,
                PluginCredentialReference.plugin_id == connection.plugin_id,
            )
        )
        if row is None:
            raise PluginServiceError(404, "Credential reference not found")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            connection.enabled = False
            state.auth_version += 1
            _audit(session, actor, "plugin.credential.revoke", connection.plugin_id, row.id)
        result = CredentialReferencesResponse(
            **await _context(session, state),
            credential_references=[CredentialReferenceItem.model_validate(row)],
        )
    return result
