"""Persistent routing metadata without user text or tool payloads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261019_0025"
down_revision = "20261018_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "routing_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("model_version", sa.String(160), nullable=True),
        sa.Column("selected_id", sa.String(160), nullable=True),
        sa.Column("fallback_reason", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("provider_calls", sa.Integer(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("phase IN ('query','capability','selection')", name="ck_routing_phase"),
        sa.CheckConstraint("mode IN ('active','shadow')", name="ck_routing_mode"),
        sa.CheckConstraint(
            "provider_calls >= 0 AND reserved_tokens >= 0", name="ck_routing_budget"
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_routing_latency"),
        sa.CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_routing_input"),
        sa.CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_routing_output"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("agent_run_id", "user_id", "created_at"):
        op.create_index(f"ix_routing_traces_{column}", "routing_traces", [column])


def downgrade():
    op.drop_table("routing_traces")
