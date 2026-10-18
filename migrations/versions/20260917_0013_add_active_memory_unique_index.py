"""Prevent duplicate active memory keys per user.

Revision ID: 20260917_0013
Revises: 20260917_0012
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0013"
down_revision: str | Sequence[str] | None = "20260917_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_user_memories_active_key",
        "user_memories",
        ["user_id", "memory_type", "memory_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_user_memories_active_key", table_name="user_memories")
