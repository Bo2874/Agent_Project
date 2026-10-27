"""Durable agent events, operation journal and approvals."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261027_0029"
down_revision = "20261027_0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_runs", sa.Column("event_sequence", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("agent_runs", sa.Column("assistant_message_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_agent_run_assistant_message",
        "agent_runs",
        "messages",
        ["assistant_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for name, extra, unique in (
        (
            "run_events",
            [
                sa.Column("sequence", sa.Integer(), nullable=False),
                sa.Column("kind", sa.String(40), nullable=False),
                sa.Column("data", postgresql.JSONB(), nullable=False),
            ],
            sa.UniqueConstraint("agent_run_id", "sequence", name="uq_run_event_sequence"),
        ),
        (
            "agent_operations",
            [
                sa.Column("key", sa.String(240), nullable=False),
                sa.Column("fingerprint", sa.String(64), nullable=False),
                sa.Column("status", sa.String(20), nullable=False),
                sa.Column("result", postgresql.JSONB(), nullable=True),
                sa.Column("actual_tokens", sa.Integer(), nullable=False, server_default="0"),
            ],
            sa.UniqueConstraint("agent_run_id", "key", name="uq_agent_operation_key"),
        ),
        (
            "agent_approvals",
            [
                sa.Column("fingerprint", sa.String(64), nullable=False),
                sa.Column("decision", sa.String(16), nullable=True),
                sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            ],
            None,
        ),
    ):
        columns = [
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "agent_run_id",
                sa.Uuid(),
                sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            *extra,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        ]
        if unique is not None:
            columns.append(unique)
        op.create_table(name, *columns)
        op.create_index(f"ix_{name}_agent_run_id", name, ["agent_run_id"])


def downgrade():
    for name in ("agent_approvals", "agent_operations", "run_events"):
        op.drop_table(name)
    op.drop_constraint("fk_agent_run_assistant_message", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "assistant_message_id")
    op.drop_column("agent_runs", "event_sequence")
