"""Persist plugin catalog history, scoped grants/connections and opaque credential references.

Revision ID: 20261018_0023
Revises: 20261004_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261018_0023"
down_revision: str | Sequence[str] | None = "20261004_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_catalog_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("auth_version", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("auth_version >= 0", name="ck_plugin_catalog_auth_version"),
        sa.CheckConstraint("id = 1", name="ck_plugin_catalog_singleton"),
        sa.CheckConstraint("revision BETWEEN 0 AND 2147483647", name="ck_plugin_catalog_revision"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "plugins",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("namespace", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(manifest_json) = 'object'", name="ck_plugins_manifest_object"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", name="uq_plugins_namespace"),
    )
    op.create_table(
        "plugin_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("manifest_version", sa.String(length=80), nullable=False),
        sa.Column(
            "capability_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(capability_ids) = 'array' AND jsonb_array_length(capability_ids) <= 256",
            name="ck_plugin_grant_capabilities",
        ),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "plugin_id", name="uq_plugin_grants_user_plugin"),
    )
    op.create_index("ix_plugin_grants_plugin_id", "plugin_grants", ["plugin_id"], unique=False)
    op.create_table(
        "plugin_manifest_releases",
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("plugin_id", "version"),
    )
    op.create_table(
        "plugin_policy_releases",
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=80), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("plugin_id", "version"),
    )
    op.create_table(
        "plugin_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("manifest_version", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "plugin_id"],
            ["plugin_grants.user_id", "plugin_grants.plugin_id"],
            name="fk_plugin_connection_grant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", "plugin_id", name="uq_plugin_connection_owner"),
        sa.UniqueConstraint("user_id", "plugin_id", name="uq_plugin_connections_user_plugin"),
    )
    op.create_index(
        "ix_plugin_connections_plugin_id", "plugin_connections", ["plugin_id"], unique=False
    )
    op.create_table(
        "plugin_credential_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("server_id", sa.String(length=16), nullable=False),
        sa.Column("slot", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("secret_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_version", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "provider IN ('encrypted_store', 'vault')", name="ck_plugin_credential_provider"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "user_id", "plugin_id"],
            ["plugin_connections.id", "plugin_connections.user_id", "plugin_connections.plugin_id"],
            name="fk_plugin_credential_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "secret_id", name="uq_plugin_credential_secret_binding"),
    )
    op.create_index(
        "uq_plugin_credential_active_slot",
        "plugin_credential_references",
        ["connection_id", "server_id", "slot"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.execute(
        sa.text("INSERT INTO plugin_catalog_state (id, revision, auth_version) VALUES (1, 0, 0)")
    )


def downgrade() -> None:
    op.drop_table("plugin_credential_references")
    op.drop_table("plugin_connections")
    op.drop_table("plugin_policy_releases")
    op.drop_table("plugin_manifest_releases")
    op.drop_table("plugin_grants")
    op.drop_table("plugins")
    op.drop_table("plugin_catalog_state")
