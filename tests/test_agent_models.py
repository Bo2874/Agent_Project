from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint

from backend.models import (
    AgentRun,
    ConversationSummary,
    EvalCaseResult,
    EvalRun,
    ModelCall,
    RunStep,
    ToolCall,
    UsageCounter,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def foreign_key_target(table_name: str, column_name: str) -> str:
    table = AgentRun.metadata.tables[table_name]
    foreign_key = next(iter(table.c[column_name].foreign_keys))
    return foreign_key.target_fullname


def test_agent_run_table_tracks_conversation_and_trigger_message() -> None:
    table = AgentRun.__table__

    assert foreign_key_target("agent_runs", "conversation_id") == "conversations.id"
    assert foreign_key_target("agent_runs", "user_message_id") == "messages.id"
    assert {"status", "current_step", "stop_reason", "error_message", "finished_at"} <= set(
        table.c.keys()
    )
    assert {
        "context_estimator",
        "context_system_tokens",
        "context_status_tokens",
        "context_memory_tokens",
        "context_history_tokens",
        "context_summary_tokens",
        "context_tool_tokens",
        "context_rag_tokens",
        "context_total_tokens",
        "context_budget_tokens",
        "context_trimmed_messages",
        "tool_selection_mode",
        "available_tool_count",
        "loaded_tool_count",
    } <= set(table.c.keys())


def test_run_step_is_ordered_and_can_link_model_call() -> None:
    table = RunStep.__table__

    assert foreign_key_target("run_steps", "agent_run_id") == "agent_runs.id"
    assert foreign_key_target("run_steps", "model_call_id") == "model_calls.id"
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("agent_run_id", "step_number", "attempt_number") in unique_columns


def test_legacy_agent_checkpoint_table_is_removed_by_current_migration() -> None:
    assert "agent_run_checkpoints" not in AgentRun.metadata.tables
    migration = (
        PROJECT_ROOT
        / "migrations/versions/20261004_0022_drop_legacy_agent_checkpoints.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "20261004_0022"' in migration
    assert 'down_revision: str | Sequence[str] | None = "20260929_0021"' in migration
    assert 'op.drop_table("agent_run_checkpoints")' in migration


def test_usage_counter_has_atomic_window_key() -> None:
    table = UsageCounter.__table__
    assert {column.name for column in table.primary_key.columns} == {
        "user_id",
        "metric",
        "window_start",
    }
    assert foreign_key_target("usage_counters", "user_id") == "users.id"
    assert "ix_usage_counters_user_metric_window" in {index.name for index in table.indexes}


def test_eval_run_tracks_dataset_model_and_prompt_versions() -> None:
    table = EvalRun.__table__
    assert {
        "dataset_id",
        "dataset_version",
        "dataset_fingerprint",
        "provider",
        "model_name",
        "prompt_version",
        "prompt_fingerprint",
        "judge_model_name",
        "judge_prompt_version",
        "selected_case_ids",
    } <= set(table.c.keys())
    assert "ix_eval_runs_version_lookup" in {index.name for index in table.indexes}


def test_eval_case_result_is_unique_per_run_and_stores_rubric_trace() -> None:
    table = EvalCaseResult.__table__
    assert foreign_key_target("eval_case_results", "eval_run_id") == "eval_runs.id"
    assert {
        "case_id",
        "category",
        "status",
        "score",
        "critical_passed",
        "actual_outcome",
        "response",
        "tool_calls",
        "citation_sources",
        "criterion_results",
        "judge_tokens",
    } <= set(table.c.keys())
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("eval_run_id", "case_id") in unique_columns


def test_model_call_tracks_prompt_version_and_cache_metrics() -> None:
    assert {
        "cached_input_tokens",
        "cache_miss_input_tokens",
        "prompt_version",
        "prompt_fingerprint",
    } <= set(ModelCall.__table__.c.keys())


def test_conversation_summary_tracks_tier_version_and_source_range() -> None:
    table = ConversationSummary.__table__

    assert foreign_key_target("conversation_summaries", "conversation_id") == "conversations.id"
    assert foreign_key_target("conversation_summaries", "source_start_message_id") == "messages.id"
    assert foreign_key_target("conversation_summaries", "source_end_message_id") == "messages.id"
    assert {
        "tier",
        "revision",
        "summary_version",
        "content",
        "source_message_count",
        "source_char_count",
        "summary_char_count",
        "superseded_by_id",
    } <= set(table.c.keys())


def test_tool_call_stores_structured_input_output_and_status() -> None:
    table = ToolCall.__table__

    assert foreign_key_target("tool_calls", "run_step_id") == "run_steps.id"
    assert {
        "provider_call_id",
        "tool_name",
        "arguments",
        "status",
        "result_content",
        "result_data",
        "error_code",
        "duration_ms",
        "approval_id",
        "approval_decision",
        "approval_requested_at",
        "approval_decided_at",
    } <= set(table.c.keys())


def test_agent_tables_have_status_and_numeric_check_constraints() -> None:
    for table in (AgentRun.__table__, RunStep.__table__, ToolCall.__table__):
        check_names = {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert any(name and name.endswith("status") for name in check_names)

    assert "ck_agent_runs_current_step" in {
        constraint.name
        for constraint in AgentRun.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_tool_calls_duration_ms" in {
        constraint.name
        for constraint in ToolCall.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
