"""Add approval audit fields to tool calls.

Revision ID: 20260916_0005
Revises: 20260915_0004
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260916_0005"
down_revision: str | Sequence[str] | None = "20260915_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_calls", sa.Column("approval_id", sa.Uuid(), nullable=True))
    op.add_column(
        "tool_calls",
        sa.Column("approval_decision", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_tool_calls_approval_id", "tool_calls", ["approval_id"])
    op.create_check_constraint(
        "ck_tool_calls_approval_decision",
        "tool_calls",
        "approval_decision IS NULL OR approval_decision IN ('approved', 'denied')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tool_calls_approval_decision", "tool_calls", type_="check")
    op.drop_constraint("uq_tool_calls_approval_id", "tool_calls", type_="unique")
    op.drop_column("tool_calls", "approval_decided_at")
    op.drop_column("tool_calls", "approval_requested_at")
    op.drop_column("tool_calls", "approval_decision")
    op.drop_column("tool_calls", "approval_id")
