"""Create durable agent run checkpoints.

Revision ID: 20260928_0018
Revises: 20260928_0017
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260928_0018"
down_revision: str | Sequence[str] | None = "20260928_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("resume_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "agent_runs", sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_agent_runs_resume_count", "agent_runs", "resume_count >= 0"
    )

    op.drop_constraint("uq_run_steps_run_number", "run_steps", type_="unique")
    op.add_column(
        "run_steps",
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        "ck_run_steps_attempt_number", "run_steps", "attempt_number >= 1"
    )
    op.create_unique_constraint(
        "uq_run_steps_run_number_attempt",
        "run_steps",
        ["agent_run_id", "step_number", "attempt_number"],
    )

    op.create_table(
        "agent_run_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("next_step", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state_size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_agent_run_checkpoints_version"),
        sa.CheckConstraint("revision >= 1", name="ck_agent_run_checkpoints_revision"),
        sa.CheckConstraint("next_step >= 1", name="ck_agent_run_checkpoints_next_step"),
        sa.CheckConstraint(
            "state_size_bytes BETWEEN 1 AND 1048576",
            name="ck_agent_run_checkpoints_state_size",
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_run_id", name="agent_run_checkpoints_agent_run_id_key"
        ),
    )
    op.create_index(
        "ix_agent_run_checkpoints_agent_run_id",
        "agent_run_checkpoints",
        ["agent_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_run_checkpoints_agent_run_id", table_name="agent_run_checkpoints"
    )
    op.drop_table("agent_run_checkpoints")
    op.drop_constraint("uq_run_steps_run_number_attempt", "run_steps", type_="unique")
    op.drop_constraint("ck_run_steps_attempt_number", "run_steps", type_="check")
    op.drop_column("run_steps", "attempt_number")
    op.create_unique_constraint(
        "uq_run_steps_run_number", "run_steps", ["agent_run_id", "step_number"]
    )
    op.drop_constraint("ck_agent_runs_resume_count", "agent_runs", type_="check")
    op.drop_column("agent_runs", "last_resumed_at")
    op.drop_column("agent_runs", "resume_count")
