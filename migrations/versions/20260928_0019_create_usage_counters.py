"""Create PostgreSQL rate-limit and quota counters.

Revision ID: 20260928_0019
Revises: 20260928_0018
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0019"
down_revision: str | Sequence[str] | None = "20260928_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("value", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "metric IN ('agent_request', 'agent_run', 'llm_token', 'tool_call')",
            name="ck_usage_counters_metric",
        ),
        sa.CheckConstraint("value >= 0", name="ck_usage_counters_value"),
        sa.CheckConstraint(
            "window_seconds > 0", name="ck_usage_counters_window_seconds"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "metric", "window_start"),
    )
    op.create_index(
        "ix_usage_counters_user_metric_window",
        "usage_counters",
        ["user_id", "metric", "window_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_counters_user_metric_window", table_name="usage_counters")
    op.drop_table("usage_counters")
