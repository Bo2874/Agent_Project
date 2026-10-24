"""Feature-gated, authenticated plugin control plane; no transport or secret-value endpoint."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_core.contracts.base import Identifier
from backend.core.config import get_settings
from backend.dependencies import CurrentAdmin, CurrentUser, DbSession
from backend.dependencies_tools import ToolRegistryDependency
from backend.schemas.plugin import (
    AdminPluginsResponse,
    AuthMutationRequest,
    BeginOAuthRequest,
    CompleteOAuthRequest,
    ConnectionEnabledRequest,
    ConnectionsResponse,
    CreateConnectionRequest,
    CreateCredentialReferenceRequest,
    CredentialReferencesResponse,
    GrantRequest,
    GrantsResponse,
    PluginEnabledRequest,
    RegisterPluginRequest,
    StoreCredentialRequest,
    UserPluginsResponse,
)
from backend.services import plugin_credentials as credentials
from backend.services import plugins as service


def require_plugin_feature() -> None:
    if not get_settings().v2_features.is_enabled("plugins"):
        raise HTTPException(status_code=404, detail="Plugin APIs are disabled")


router = APIRouter(tags=["plugins"], dependencies=[Depends(require_plugin_feature)])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.get("/api/capabilities")
async def discover_capabilities(
    actor: CurrentUser,
    registry: ToolRegistryDependency,
    q: str = Query(default="", max_length=4096),
    kind: Literal["tool", "skill", "retriever", "workflow"] | None = None,
    plugin_id: str | None = Query(default=None, max_length=128),
    tag: Annotated[list[str] | None, Query(max_length=20)] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> dict:
    """Host runtime metadata only; persisted private connections are not published here."""
    catalog = registry.capability_catalog(actor.role)
    matches = catalog.search(q, kind=kind, plugin_id=plugin_id, tags=tuple(tag or ()), limit=4096)
    return {
        "catalog_version": catalog.version,
        "scope": "host_runtime",
        "total": len(matches),
        "limit": limit,
        "offset": offset,
        "capabilities": [
            {"binding_name": row.binding_name, "descriptor": row.descriptor.model_dump(mode="json")}
            for row in matches[offset : offset + limit]
        ],
    }


@router.post(
    "/api/plugin-connections/{connection_id}/credentials",
    response_model=CredentialReferencesResponse,
)
async def store_credential(
    connection_id: UUID, body: StoreCredentialRequest, session: DbSession, actor: CurrentUser
):
    return await credentials.set_static(
        session,
        actor,
        connection_id,
        body.server_id,
        body.slot,
        body.value.get_secret_value(),
        body.expected_auth_version,
    )


@router.post("/api/plugin-connections/{connection_id}/oauth/begin")
async def begin_oauth(
    connection_id: UUID, body: BeginOAuthRequest, session: DbSession, actor: CurrentUser
):
    return await credentials.begin_oauth(
        session, actor, connection_id, body.server_id, body.slot, body.expected_auth_version
    )


@router.post(
    "/api/plugin-connections/{connection_id}/oauth/complete",
    response_model=CredentialReferencesResponse,
)
async def complete_oauth(
    connection_id: UUID, body: CompleteOAuthRequest, session: DbSession, actor: CurrentUser
):
    return await credentials.complete_oauth(
        session,
        actor,
        connection_id,
        body.state.get_secret_value(),
        body.code.get_secret_value(),
        body.issuer,
    )


@router.get(
    "/api/plugins/oauth/callback",
    response_model=CredentialReferencesResponse,
)
async def oauth_callback(
    session: DbSession,
    actor: CurrentUser,
    state: Annotated[str, Query(min_length=1, max_length=4096)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    iss: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
):
    if error or not code:
        raise HTTPException(status_code=409, detail="OAuth authorization was not completed")
    return await credentials.complete_state(session, actor, state, code, iss)


@router.post("/api/plugins/oauth/complete", response_model=CredentialReferencesResponse)
async def complete_oauth_state(body: CompleteOAuthRequest, session: DbSession, actor: CurrentUser):
    """Browser callback exchanges once via a body, with existing owner-bound state checks."""
    return await credentials.complete_state(
        session, actor, body.state.get_secret_value(), body.code.get_secret_value(), body.issuer
    )


@router.post("/api/plugin-connections/{connection_id}/credentials/{reference_id}/revoke")
async def revoke_credential(
    connection_id: UUID,
    reference_id: UUID,
    body: AuthMutationRequest,
    session: DbSession,
    actor: CurrentUser,
):
    return await credentials.revoke(
        session, actor, connection_id, reference_id, body.expected_auth_version
    )


@router.get("/api/admin/plugins", response_model=AdminPluginsResponse)
async def admin_plugins(
    session: DbSession, actor: CurrentAdmin, limit: Limit = 50, offset: Offset = 0
):
    return await service.admin_plugins(session, actor, limit=limit, offset=offset)


@router.put("/api/admin/plugins/{plugin_id}", response_model=AdminPluginsResponse)
async def register_plugin(
    plugin_id: Identifier, body: RegisterPluginRequest, session: DbSession, actor: CurrentAdmin
):
    if plugin_id != body.manifest.id:
        raise HTTPException(status_code=422, detail="Manifest id must match the URL")
    return await service.register_plugin(
        session, actor, body.manifest, body.expected_catalog_version
    )


@router.patch("/api/admin/plugins/{plugin_id}/enabled", response_model=AdminPluginsResponse)
async def plugin_enabled(
    plugin_id: Identifier, body: PluginEnabledRequest, session: DbSession, actor: CurrentAdmin
):
    return await service.set_plugin_enabled(
        session, actor, plugin_id, body.enabled, body.expected_catalog_version
    )


@router.get("/api/admin/plugins/{plugin_id}/grants", response_model=GrantsResponse)
async def grants(
    plugin_id: Identifier,
    session: DbSession,
    actor: CurrentAdmin,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return await service.list_grants(session, actor, plugin_id, limit=limit, offset=offset)


@router.put("/api/admin/plugins/{plugin_id}/grants/{user_id}", response_model=GrantsResponse)
async def grant(
    plugin_id: Identifier,
    user_id: UUID,
    body: GrantRequest,
    session: DbSession,
    actor: CurrentAdmin,
):
    return await service.set_grant(
        session,
        actor,
        plugin_id,
        user_id,
        body.enabled,
        body.capability_ids,
        body.expected_auth_version,
    )


@router.get("/api/plugins", response_model=UserPluginsResponse)
async def user_plugins(
    session: DbSession, actor: CurrentUser, limit: Limit = 50, offset: Offset = 0
):
    return await service.user_plugins(session, actor, limit=limit, offset=offset)


@router.get("/api/plugin-connections", response_model=ConnectionsResponse)
async def connections(
    session: DbSession, actor: CurrentUser, limit: Limit = 50, offset: Offset = 0
):
    return await service.list_connections(session, actor, limit=limit, offset=offset)


@router.get("/api/plugin-connections/{connection_id}", response_model=ConnectionsResponse)
async def connection(connection_id: UUID, session: DbSession, actor: CurrentUser):
    return await service.list_connections(session, actor, connection_id=connection_id)


@router.post("/api/plugin-connections", response_model=ConnectionsResponse, status_code=201)
async def create_connection(body: CreateConnectionRequest, session: DbSession, actor: CurrentUser):
    return await service.create_connection(
        session, actor, body.plugin_id, body.expected_auth_version
    )


@router.patch("/api/plugin-connections/{connection_id}/enabled", response_model=ConnectionsResponse)
async def connection_enabled(
    connection_id: UUID, body: ConnectionEnabledRequest, session: DbSession, actor: CurrentUser
):
    return await service.set_connection_enabled(
        session, actor, connection_id, body.enabled, body.expected_auth_version
    )


@router.post(
    "/api/admin/plugin-connections/{connection_id}/credential-references",
    response_model=CredentialReferencesResponse,
    status_code=201,
)
async def provision_reference(
    connection_id: UUID,
    body: CreateCredentialReferenceRequest,
    session: DbSession,
    actor: CurrentAdmin,
):
    return await service.provision_reference(
        session,
        actor,
        connection_id,
        body.server_id,
        body.slot,
        body.provider,
        body.secret_id,
        body.expected_auth_version,
    )


@router.post(
    "/api/plugin-connections/{connection_id}/credential-references/{reference_id}/revoke",
    response_model=CredentialReferencesResponse,
)
async def revoke_reference(
    connection_id: UUID,
    reference_id: UUID,
    body: AuthMutationRequest,
    session: DbSession,
    actor: CurrentUser,
):
    return await service.revoke_reference(
        session, actor, connection_id, reference_id, body.expected_auth_version
    )
