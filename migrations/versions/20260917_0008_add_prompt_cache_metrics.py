"""Add prompt version and cache metrics.

Revision ID: 20260917_0008
Revises: 20260917_0007
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0008"
down_revision: str | Sequence[str] | None = "20260917_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_calls",
        sa.Column("cache_miss_input_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "model_calls",
        sa.Column("prompt_version", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "model_calls",
        sa.Column("prompt_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_calls", "prompt_fingerprint")
    op.drop_column("model_calls", "prompt_version")
    op.drop_column("model_calls", "cache_miss_input_tokens")
