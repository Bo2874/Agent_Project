from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    field_validator,
    model_validator,
)

from agent_core.contracts.base import Identifier, Version, require_unique
from agent_core.plugins import PluginManifest
from agent_core.plugins.namespace import ServerId

AuthVersion = Annotated[int, Field(strict=True, ge=0)]
CapabilityIds = Annotated[tuple[Identifier, ...], Field(max_length=256)]


class PluginAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, hide_input_in_errors=True)


class VersionContext(PluginAPIModel):
    catalog_version: Version
    auth_version: AuthVersion


class RegisterPluginRequest(PluginAPIModel):
    manifest: PluginManifest
    expected_catalog_version: Version


class PluginEnabledRequest(PluginAPIModel):
    enabled: StrictBool
    expected_catalog_version: Version


class AuthMutationRequest(PluginAPIModel):
    expected_auth_version: AuthVersion


class GrantRequest(AuthMutationRequest):
    enabled: StrictBool
    capability_ids: CapabilityIds = ()

    @model_validator(mode="after")
    def validate_scope(self):
        require_unique(self.capability_ids, "capability_ids")
        if self.enabled and not self.capability_ids:
            raise ValueError("An enabled grant requires explicit capability ids")
        return self


class CreateConnectionRequest(AuthMutationRequest):
    plugin_id: Identifier


class ConnectionEnabledRequest(AuthMutationRequest):
    enabled: StrictBool


class CreateCredentialReferenceRequest(AuthMutationRequest):
    server_id: ServerId
    slot: Identifier
    provider: Literal["encrypted_store", "vault"]
    secret_id: UUID


class StoreCredentialRequest(AuthMutationRequest):
    server_id: ServerId
    slot: Identifier
    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_token(cls, value):
        token = value.get_secret_value()
        if not 1 <= len(token) <= 16384 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError("Invalid credential value")
        return value


class BeginOAuthRequest(AuthMutationRequest):
    server_id: ServerId
    slot: Identifier


class CompleteOAuthRequest(PluginAPIModel):
    state: SecretStr
    code: SecretStr
    issuer: str | None = None

    @field_validator("state", "code")
    @classmethod
    def validate_oauth_value(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 4096:
            raise ValueError("Invalid OAuth response")
        return value


class PluginAdminItem(PluginAPIModel):
    manifest: PluginManifest
    enabled: bool


class AdminPluginsResponse(VersionContext):
    plugins: list[PluginAdminItem]


class GrantItem(PluginAPIModel):
    user_id: UUID
    plugin_id: str
    enabled: bool
    manifest_version: str
    capability_ids: tuple[str, ...]


class GrantsResponse(VersionContext):
    grants: list[GrantItem]


class CredentialReferenceItem(PluginAPIModel):
    id: UUID
    connection_id: UUID
    server_id: str
    slot: str
    manifest_version: str
    created_at: datetime
    revoked_at: datetime | None


class CredentialReferencesResponse(VersionContext):
    credential_references: list[CredentialReferenceItem]


class ConnectionItem(PluginAPIModel):
    id: UUID
    plugin_id: str
    enabled: bool
    manifest_version: str
    effective_enabled: bool
    status: Literal[
        "configured",
        "connection_disabled",
        "plugin_disabled",
        "grant_revoked",
        "version_mismatch",
        "credentials_missing",
    ]
    runtime_connected: Literal[False] = False
    credential_references: list[CredentialReferenceItem]


class ConnectionsResponse(VersionContext):
    connections: list[ConnectionItem]


class CredentialRequirementItem(PluginAPIModel):
    server_id: str
    slot: str
    kind: Literal["api_key", "bearer_token", "oauth2"]
    transport: Literal["stdio", "streamable_http"]


class UserPluginItem(PluginAPIModel):
    id: str
    namespace: str
    name: str
    description: str
    version: str
    plugin_enabled: bool
    grant_enabled: bool
    grant_manifest_version: str
    capability_ids: tuple[str, ...]
    credential_requirements: list[CredentialRequirementItem] = Field(default_factory=list)


class UserPluginsResponse(VersionContext):
    plugins: list[UserPluginItem]
