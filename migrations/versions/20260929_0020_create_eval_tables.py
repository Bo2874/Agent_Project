"""Create versioned evaluation run and case result tables.

Revision ID: 20260929_0020
Revises: 20260928_0019
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260929_0020"
down_revision: str | Sequence[str] | None = "20260928_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.String(length=80), nullable=False),
        sa.Column("dataset_version", sa.String(length=32), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("judge_provider", sa.String(length=40), nullable=False),
        sa.Column("judge_model_name", sa.String(length=100), nullable=False),
        sa.Column("judge_prompt_version", sa.String(length=80), nullable=False),
        sa.Column("judge_prompt_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("completed_cases", sa.Integer(), server_default="0", nullable=False),
        sa.Column("selected_case_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'cancelled')",
            name="ck_eval_runs_status",
        ),
        sa.CheckConstraint("total_cases >= 1", name="ck_eval_runs_total_cases"),
        sa.CheckConstraint(
            "completed_cases BETWEEN 0 AND total_cases",
            name="ck_eval_runs_completed_cases",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_runs_dataset_id", "eval_runs", ["dataset_id"])
    op.create_index("ix_eval_runs_model_name", "eval_runs", ["model_name"])
    op.create_index("ix_eval_runs_prompt_version", "eval_runs", ["prompt_version"])
    op.create_index("ix_eval_runs_status", "eval_runs", ["status"])
    op.create_index(
        "ix_eval_runs_version_lookup",
        "eval_runs",
        ["dataset_id", "dataset_version", "model_name", "prompt_version", "created_at"],
    )

    op.create_table(
        "eval_case_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("eval_run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("critical_passed", sa.Boolean(), nullable=True),
        sa.Column("actual_outcome", sa.String(length=16), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "citation_sources",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "criterion_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("judge_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed', 'error', 'skipped')",
            name="ck_eval_case_results_status",
        ),
        sa.CheckConstraint(
            "category IN ('chat', 'tool', 'rag', 'safety')",
            name="ck_eval_case_results_category",
        ),
        sa.CheckConstraint(
            "actual_outcome IS NULL OR actual_outcome IN "
            "('answer', 'clarify', 'abstain', 'refuse')",
            name="ck_eval_case_results_outcome",
        ),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_eval_case_results_score",
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0 "
            "AND judge_tokens >= 0 AND latency_ms >= 0",
            name="ck_eval_case_results_metrics",
        ),
        sa.ForeignKeyConstraint(["eval_run_id"], ["eval_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("eval_run_id", "case_id", name="uq_eval_case_results_run_case"),
    )
    op.create_index("ix_eval_case_results_eval_run_id", "eval_case_results", ["eval_run_id"])
    op.create_index("ix_eval_case_results_case_id", "eval_case_results", ["case_id"])
    op.create_index("ix_eval_case_results_category", "eval_case_results", ["category"])
    op.create_index("ix_eval_case_results_status", "eval_case_results", ["status"])


def downgrade() -> None:
    op.drop_index("ix_eval_case_results_status", table_name="eval_case_results")
    op.drop_index("ix_eval_case_results_category", table_name="eval_case_results")
    op.drop_index("ix_eval_case_results_case_id", table_name="eval_case_results")
    op.drop_index("ix_eval_case_results_eval_run_id", table_name="eval_case_results")
    op.drop_table("eval_case_results")
    op.drop_index("ix_eval_runs_version_lookup", table_name="eval_runs")
    op.drop_index("ix_eval_runs_status", table_name="eval_runs")
    op.drop_index("ix_eval_runs_prompt_version", table_name="eval_runs")
    op.drop_index("ix_eval_runs_model_name", table_name="eval_runs")
    op.drop_index("ix_eval_runs_dataset_id", table_name="eval_runs")
    op.drop_table("eval_runs")
