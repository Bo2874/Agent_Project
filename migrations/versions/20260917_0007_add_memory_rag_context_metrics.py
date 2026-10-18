"""Add memory and RAG context metrics.

Revision ID: 20260917_0007
Revises: 20260917_0006
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0007"
down_revision: str | Sequence[str] | None = "20260917_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("context_memory_tokens", sa.Integer(), nullable=True))
    op.add_column("agent_runs", sa.Column("context_rag_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "context_rag_tokens")
    op.drop_column("agent_runs", "context_memory_tokens")
