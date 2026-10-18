"""Add status context metrics.

Revision ID: 20260917_0010
Revises: 20260917_0009
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0010"
down_revision: str | Sequence[str] | None = "20260917_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("context_status_tokens", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "context_status_tokens")
