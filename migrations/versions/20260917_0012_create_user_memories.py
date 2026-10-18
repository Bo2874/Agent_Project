"""Create structured user memories.

Revision ID: 20260917_0012
Revises: 20260917_0011
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0012"
down_revision: str | Sequence[str] | None = "20260917_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("memory_type", sa.String(length=24), nullable=False),
        sa.Column("memory_key", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("source_tool_call_id", sa.Uuid(), nullable=True),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column(
            "confidence",
            sa.Numeric(precision=4, scale=3),
            server_default=sa.text("1.000"),
            nullable=False,
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default="active", nullable=False
        ),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint(
            "memory_type IN ('fact', 'preference', 'goal', 'constraint')",
            name="ck_user_memories_type",
        ),
        sa.CheckConstraint(
            "source_type IN ('user_message', 'tool_result', 'manual', 'import')",
            name="ck_user_memories_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'deleted')",
            name="ck_user_memories_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_user_memories_confidence",
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_until IS NULL OR valid_until > valid_from",
            name="ck_user_memories_valid_range",
        ),
        sa.CheckConstraint(
            "(source_type = 'user_message' AND source_message_id IS NOT NULL "
            "AND source_tool_call_id IS NULL) OR "
            "(source_type = 'tool_result' AND source_tool_call_id IS NOT NULL "
            "AND source_message_id IS NULL) OR "
            "(source_type IN ('manual', 'import') AND source_message_id IS NULL "
            "AND source_tool_call_id IS NULL)",
            name="ck_user_memories_source_link",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_tool_call_id"], ["tool_calls.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["user_memories.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_memories_user_key",
        "user_memories",
        ["user_id", "memory_key"],
    )
    op.create_index(
        "ix_user_memories_user_type_status",
        "user_memories",
        ["user_id", "memory_type", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_memories_user_type_status", table_name="user_memories")
    op.drop_index("ix_user_memories_user_key", table_name="user_memories")
    op.drop_table("user_memories")
