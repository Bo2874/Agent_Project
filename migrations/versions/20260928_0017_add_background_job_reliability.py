"""Add cancellation, controlled retry, lease and idempotency to background jobs.

Revision ID: 20260928_0017
Revises: 20260928_0016
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0017"
down_revision: str | Sequence[str] | None = "20260928_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_background_jobs_status", "background_jobs", type_="check")
    op.create_check_constraint(
        "ck_background_jobs_status",
        "background_jobs",
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
    )
    op.add_column(
        "background_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "background_jobs",
        sa.Column("max_attempts", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "background_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_jobs", sa.Column("idempotency_scope", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "background_jobs", sa.Column("idempotency_key", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "background_jobs",
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "background_jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "background_jobs", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_background_jobs_attempts",
        "background_jobs",
        "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 5",
    )
    op.create_check_constraint(
        "ck_background_jobs_idempotency_complete",
        "background_jobs",
        "(idempotency_key IS NULL AND idempotency_scope IS NULL "
        "AND idempotency_fingerprint IS NULL) OR "
        "(idempotency_key IS NOT NULL AND idempotency_scope IS NOT NULL "
        "AND idempotency_fingerprint IS NOT NULL)",
    )
    op.create_index(
        "uq_background_jobs_idempotency",
        "background_jobs",
        ["queue_name", "idempotency_scope", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_background_jobs_lease_recovery",
        "background_jobs",
        ["queue_name", "status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_background_jobs_lease_recovery", table_name="background_jobs")
    op.drop_index("uq_background_jobs_idempotency", table_name="background_jobs")
    op.drop_constraint(
        "ck_background_jobs_idempotency_complete", "background_jobs", type_="check"
    )
    op.drop_constraint("ck_background_jobs_attempts", "background_jobs", type_="check")
    op.drop_column("background_jobs", "cancelled_at")
    op.drop_column("background_jobs", "cancel_requested_at")
    op.drop_column("background_jobs", "idempotency_fingerprint")
    op.drop_column("background_jobs", "idempotency_key")
    op.drop_column("background_jobs", "idempotency_scope")
    op.drop_column("background_jobs", "lease_expires_at")
    op.drop_column("background_jobs", "max_attempts")
    op.drop_column("background_jobs", "attempt_count")
    op.drop_constraint("ck_background_jobs_status", "background_jobs", type_="check")
    op.create_check_constraint(
        "ck_background_jobs_status",
        "background_jobs",
        "status IN ('queued', 'running', 'succeeded', 'failed')",
    )
