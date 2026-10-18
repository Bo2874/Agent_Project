"""Drop the legacy application-owned agent checkpoint table.

Revision ID: 20261004_0022
Revises: 20260929_0021
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261004_0022"
down_revision: str | Sequence[str] | None = "20260929_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_agent_run_checkpoints_agent_run_id",
        table_name="agent_run_checkpoints",
    )
    op.drop_table("agent_run_checkpoints")


def downgrade() -> None:
    op.create_table(
        "agent_run_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("next_step", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("state_size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_agent_run_checkpoints_version"),
        sa.CheckConstraint("revision >= 1", name="ck_agent_run_checkpoints_revision"),
        sa.CheckConstraint("next_step >= 1", name="ck_agent_run_checkpoints_next_step"),
        sa.CheckConstraint(
            "state_size_bytes BETWEEN 1 AND 1048576",
            name="ck_agent_run_checkpoints_state_size",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_run_id",
            name="agent_run_checkpoints_agent_run_id_key",
        ),
    )
    op.create_index(
        "ix_agent_run_checkpoints_agent_run_id",
        "agent_run_checkpoints",
        ["agent_run_id"],
        unique=True,
    )
