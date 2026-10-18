"""Add memory sensitivity classification and retention deadline.

Revision ID: 20260917_0015
Revises: 20260917_0014
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0015"
down_revision: str | Sequence[str] | None = "20260917_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_memories",
        sa.Column(
            "sensitivity",
            sa.String(length=20),
            server_default="personal",
            nullable=False,
        ),
    )
    op.add_column(
        "user_memories",
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE user_memories "
        "SET retention_until = observed_at + INTERVAL '180 days' "
        "WHERE retention_until IS NULL"
    )
    op.alter_column("user_memories", "retention_until", nullable=False)
    op.create_check_constraint(
        "ck_user_memories_sensitivity",
        "user_memories",
        "sensitivity IN ('normal', 'personal', 'sensitive')",
    )
    op.create_index(
        "ix_user_memories_user_retention",
        "user_memories",
        ["user_id", "retention_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_memories_user_retention", table_name="user_memories")
    op.drop_constraint(
        "ck_user_memories_sensitivity",
        "user_memories",
        type_="check",
    )
    op.drop_column("user_memories", "retention_until")
    op.drop_column("user_memories", "sensitivity")
