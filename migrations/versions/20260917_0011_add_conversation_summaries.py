"""Add versioned conversation summaries.

Revision ID: 20260917_0011
Revises: 20260917_0010
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0011"
down_revision: str | Sequence[str] | None = "20260917_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("summary_version", sa.String(length=80), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_start_message_id", sa.Uuid(), nullable=True),
        sa.Column("source_end_message_id", sa.Uuid(), nullable=True),
        sa.Column("source_message_count", sa.Integer(), nullable=False),
        sa.Column("source_char_count", sa.Integer(), nullable=False),
        sa.Column("summary_char_count", sa.Integer(), nullable=False),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("tier >= 1", name="ck_conversation_summaries_tier"),
        sa.CheckConstraint("revision >= 1", name="ck_conversation_summaries_revision"),
        sa.CheckConstraint(
            "source_message_count >= 1",
            name="ck_conversation_summaries_source_message_count",
        ),
        sa.CheckConstraint(
            "source_char_count >= summary_char_count",
            name="ck_conversation_summaries_compressed_chars",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_start_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_end_message_id"], ["messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["conversation_summaries.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "revision",
            name="uq_conversation_summaries_revision",
        ),
    )
    op.create_index(
        "ix_conversation_summaries_conversation_id",
        "conversation_summaries",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_summaries_superseded_by_id",
        "conversation_summaries",
        ["superseded_by_id"],
    )
    op.add_column(
        "agent_runs", sa.Column("context_summary_tokens", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "context_summary_tokens")
    op.drop_index(
        "ix_conversation_summaries_superseded_by_id",
        table_name="conversation_summaries",
    )
    op.drop_index(
        "ix_conversation_summaries_conversation_id",
        table_name="conversation_summaries",
    )
    op.drop_table("conversation_summaries")
