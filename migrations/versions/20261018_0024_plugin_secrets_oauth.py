"""Encrypted connection credentials and one-time OAuth state.
Revision ID: 20261018_0024
Revises: 20261018_0023
"""

import sqlalchemy as sa
from alembic import op

revision = "20261018_0024"
down_revision = "20261018_0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plugin_secrets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("server_id", sa.String(length=16), nullable=False),
        sa.Column("slot", sa.String(length=128), nullable=False),
        sa.Column("manifest_version", sa.String(length=80), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('api_key','bearer_token','oauth2')", name="ck_plugin_secret_kind"
        ),
        sa.CheckConstraint(
            "octet_length(ciphertext) BETWEEN 1 AND 131072", name="ck_plugin_secret_size"
        ),
        sa.CheckConstraint("revision >= 0", name="ck_plugin_secret_revision"),
        sa.ForeignKeyConstraint(
            ["connection_id", "user_id", "plugin_id"],
            ["plugin_connections.id", "plugin_connections.user_id", "plugin_connections.plugin_id"],
            name="fk_plugin_secret_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_plugin_secrets_connection", "plugin_secrets", ["connection_id"], unique=False
    )
    op.create_table(
        "plugin_oauth_states",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("server_id", sa.String(length=16), nullable=False),
        sa.Column("slot", sa.String(length=128), nullable=False),
        sa.Column("manifest_version", sa.String(length=80), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "octet_length(ciphertext) BETWEEN 1 AND 131072", name="ck_plugin_oauth_state_size"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "user_id", "plugin_id"],
            ["plugin_connections.id", "plugin_connections.user_id", "plugin_connections.plugin_id"],
            name="fk_plugin_oauth_state_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index(
        "ix_plugin_oauth_states_expires", "plugin_oauth_states", ["expires_at"], unique=False
    )


def downgrade():
    op.drop_table("plugin_oauth_states")
    op.drop_table("plugin_secrets")
