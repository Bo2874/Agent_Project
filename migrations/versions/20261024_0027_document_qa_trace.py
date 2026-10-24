"""Persist bounded RAG query, coverage and model/retrieval provenance on each run."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261024_0027"
down_revision = "20261024_0026"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("rag_trace", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_agent_runs_rag_trace",
        "agent_runs",
        "rag_trace IS NULL OR jsonb_typeof(rag_trace) = 'object'",
    )


def downgrade():
    op.drop_constraint("ck_agent_runs_rag_trace", "agent_runs", type_="check")
    op.drop_column("agent_runs", "rag_trace")
