"""Add user-controlled memory pinning.

Revision ID: 20260917_0014
Revises: 20260917_0013
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0014"
down_revision: str | Sequence[str] | None = "20260917_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_memories",
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user_memories", "is_pinned")
