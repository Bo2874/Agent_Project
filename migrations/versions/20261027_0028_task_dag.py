"""Sequential task DAG audit projections; checkpoint remains authoritative."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261027_0028"
down_revision = "20261024_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "agent_run_id",
            sa.Uuid(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "agent_run_id", "plan_version", "task_id", name="uq_agent_task_identity"
        ),
    )
    op.create_index("ix_agent_tasks_agent_run_id", "agent_tasks", ["agent_run_id"])
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "agent_task_id",
            sa.Uuid(),
            sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("first_step", sa.Integer(), nullable=False),
        sa.Column("last_step", sa.Integer(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("agent_task_id", "attempt_id", name="uq_task_attempt_identity"),
    )
    op.create_index("ix_task_attempts_agent_task_id", "task_attempts", ["agent_task_id"])


def downgrade():
    op.drop_table("task_attempts")
    op.drop_table("agent_tasks")
