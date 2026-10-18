"""Add a structured stop reason to agent runs.

Revision ID: 20260915_0004
Revises: 20260915_0003
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260915_0004"
down_revision: str | Sequence[str] | None = "20260915_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("stop_reason", sa.String(length=80), nullable=True))
    op.create_index("ix_agent_runs_stop_reason", "agent_runs", ["stop_reason"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_stop_reason", table_name="agent_runs")
    op.drop_column("agent_runs", "stop_reason")
