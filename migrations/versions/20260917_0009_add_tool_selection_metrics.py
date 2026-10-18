"""Add tool selection metrics.

Revision ID: 20260917_0009
Revises: 20260917_0008
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0009"
down_revision: str | Sequence[str] | None = "20260917_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("tool_selection_mode", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("available_tool_count", sa.Integer(), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("loaded_tool_count", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "loaded_tool_count")
    op.drop_column("agent_runs", "available_tool_count")
    op.drop_column("agent_runs", "tool_selection_mode")
