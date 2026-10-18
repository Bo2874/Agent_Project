"""Add context token metrics to agent runs.

Revision ID: 20260917_0006
Revises: 20260916_0005
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0006"
down_revision: str | Sequence[str] | None = "20260916_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("context_estimator", sa.String(40), nullable=True))
    op.add_column("agent_runs", sa.Column("context_system_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("context_history_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("context_tool_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("context_total_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("context_budget_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("context_trimmed_messages", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_agent_runs_context_total_tokens",
        "agent_runs",
        "context_total_tokens IS NULL OR context_total_tokens >= 0",
    )
    op.create_check_constraint(
        "ck_agent_runs_context_trimmed_messages",
        "agent_runs",
        "context_trimmed_messages >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_runs_context_trimmed_messages",
        "agent_runs",
        type_="check",
    )
    op.drop_constraint(
        "ck_agent_runs_context_total_tokens",
        "agent_runs",
        type_="check",
    )
    op.drop_column("agent_runs", "context_trimmed_messages")
    op.drop_column("agent_runs", "context_budget_tokens")
    op.drop_column("agent_runs", "context_total_tokens")
    op.drop_column("agent_runs", "context_tool_tokens")
    op.drop_column("agent_runs", "context_history_tokens")
    op.drop_column("agent_runs", "context_system_tokens")
    op.drop_column("agent_runs", "context_estimator")
