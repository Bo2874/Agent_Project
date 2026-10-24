"""Authoritative plugin configuration and owner-scoped connection metadata; no secrets."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class PluginTimestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PluginCatalogState(Base):
    __tablename__ = "plugin_catalog_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_plugin_catalog_singleton"),
        CheckConstraint("revision BETWEEN 0 AND 2147483647", name="ck_plugin_catalog_revision"),
        CheckConstraint("auth_version >= 0", name="ck_plugin_catalog_auth_version"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    auth_version: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class Plugin(PluginTimestamps, Base):
    __tablename__ = "plugins"
    __table_args__ = (
        UniqueConstraint("namespace", name="uq_plugins_namespace"),
        CheckConstraint(
            "jsonb_typeof(manifest_json) = 'object'", name="ck_plugins_manifest_object"
        ),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(80))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class PluginManifestRelease(Base):
    __tablename__ = "plugin_manifest_releases"
    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PluginPolicyRelease(Base):
    __tablename__ = "plugin_policy_releases"
    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("plugins.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PluginGrant(PluginTimestamps, Base):
    __tablename__ = "plugin_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "plugin_id", name="uq_plugin_grants_user_plugin"),
        CheckConstraint(
            "jsonb_typeof(capability_ids) = 'array' AND jsonb_array_length(capability_ids) <= 256",
            name="ck_plugin_grant_capabilities",
        ),
        Index("ix_plugin_grants_plugin_id", "plugin_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    plugin_id: Mapped[str] = mapped_column(ForeignKey("plugins.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    manifest_version: Mapped[str] = mapped_column(String(80))
    capability_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PluginConnection(PluginTimestamps, Base):
    __tablename__ = "plugin_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "plugin_id"],
            ["plugin_grants.user_id", "plugin_grants.plugin_id"],
            ondelete="CASCADE",
            name="fk_plugin_connection_grant",
        ),
        UniqueConstraint("user_id", "plugin_id", name="uq_plugin_connections_user_plugin"),
        UniqueConstraint("id", "user_id", "plugin_id", name="uq_plugin_connection_owner"),
        Index("ix_plugin_connections_plugin_id", "plugin_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column()
    plugin_id: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    manifest_version: Mapped[str] = mapped_column(String(80))


class PluginCredentialReference(Base):
    __tablename__ = "plugin_credential_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "user_id", "plugin_id"],
            ["plugin_connections.id", "plugin_connections.user_id", "plugin_connections.plugin_id"],
            ondelete="CASCADE",
            name="fk_plugin_credential_owner",
        ),
        UniqueConstraint("provider", "secret_id", name="uq_plugin_credential_secret_binding"),
        CheckConstraint(
            "provider IN ('encrypted_store', 'vault')", name="ck_plugin_credential_provider"
        ),
        Index(
            "uq_plugin_credential_active_slot",
            "connection_id",
            "server_id",
            "slot",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column()
    user_id: Mapped[uuid.UUID] = mapped_column()
    plugin_id: Mapped[str] = mapped_column(String(128))
    server_id: Mapped[str] = mapped_column(String(16))
    slot: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32))
    secret_id: Mapped[uuid.UUID] = mapped_column()
    manifest_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
