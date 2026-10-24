"""Encrypted connection credentials and one-time OAuth state; plaintext never reaches ORM fields."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class SecretOwner:
    connection_id: Mapped[uuid.UUID] = mapped_column()
    user_id: Mapped[uuid.UUID] = mapped_column()
    plugin_id: Mapped[str] = mapped_column(String(128))
    server_id: Mapped[str] = mapped_column(String(16))
    slot: Mapped[str] = mapped_column(String(128))
    manifest_version: Mapped[str] = mapped_column(String(80))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)


def owner_fk(name):
    return ForeignKeyConstraint(
        ["connection_id", "user_id", "plugin_id"],
        ["plugin_connections.id", "plugin_connections.user_id", "plugin_connections.plugin_id"],
        ondelete="CASCADE",
        name=name,
    )


class PluginSecret(SecretOwner, Base):
    __tablename__ = "plugin_secrets"
    __table_args__ = (
        owner_fk("fk_plugin_secret_owner"),
        CheckConstraint("revision >= 0", name="ck_plugin_secret_revision"),
        CheckConstraint(
            "kind IN ('api_key','bearer_token','oauth2')", name="ck_plugin_secret_kind"
        ),
        CheckConstraint(
            "octet_length(ciphertext) BETWEEN 1 AND 131072", name="ck_plugin_secret_size"
        ),
        Index("ix_plugin_secrets_connection", "connection_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_fingerprint: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PluginOAuthState(SecretOwner, Base):
    __tablename__ = "plugin_oauth_states"
    __table_args__ = (
        owner_fk("fk_plugin_oauth_state_owner"),
        CheckConstraint(
            "octet_length(ciphertext) BETWEEN 1 AND 131072", name="ck_plugin_oauth_state_size"
        ),
        Index("ix_plugin_oauth_states_expires", "expires_at"),
    )
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
