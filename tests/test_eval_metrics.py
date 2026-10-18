from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agent_core.eval_metrics import calculate_eval_metrics, classify_failure


@dataclass
class Result:
    category: str
    status: str
    critical_passed: bool | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    criterion_results: list[dict[str, Any]] = field(default_factory=list)


def test_calculates_pass_rate_tool_success_and_primary_failure_categories() -> None:
    results = [
        Result(
            category="chat",
            status="passed",
            critical_passed=True,
            tool_calls=[
                {
                    "name": "demo__calculate",
                    "executed": True,
                    "error_code": None,
                }
            ],
        ),
        Result(
            category="tool",
            status="failed",
            critical_passed=False,
            tool_calls=[
                {
                    "name": "demo__count_text",
                    "executed": True,
                    "error_code": "tool_execution_failed",
                },
                {
                    "name": "demo__simulate_sensitive_action",
                    "executed": False,
                    "error_code": "tool_approval_denied",
                },
            ],
            criterion_results=[
                {
                    "evaluator": "tool_trace",
                    "passed": False,
                    "critical": True,
                    "reason": "thiếu tool ['demo__count_text']",
                }
            ],
        ),
        Result(category="rag", status="error"),
        Result(category="safety", status="skipped"),
    ]

    metrics = calculate_eval_metrics(results, total_expected=5)

    assert metrics.total_results == 4
    assert metrics.missing_cases == 1
    assert metrics.attempted_cases == 3
    assert metrics.passed_cases == 1
    assert metrics.pass_rate == pytest.approx(1 / 3)
    assert metrics.tool_attempts == 3
    assert metrics.tool_executions == 2
    assert metrics.tool_successes == 1
    assert metrics.tool_failures == 1
    assert metrics.tool_blocked == 1
    assert metrics.tool_success_rate == 0.5
    assert metrics.failure_categories == {
        "execution_error": 1,
        "missing_tool": 1,
    }
    assert metrics.categories["chat"].pass_rate == 1.0
    assert metrics.categories["tool"].pass_rate == 0.0
    assert metrics.categories["rag"].errors == 1
    assert metrics.categories["safety"].pass_rate is None


@pytest.mark.parametrize(
    ("evaluator", "reason", "expected"),
    [
        ("outcome", "wrong", "wrong_outcome"),
        ("response_contains", "missing", "missing_expected_content"),
        ("response_excludes", "present", "forbidden_content"),
        ("citations", "missing", "citation_failure"),
        ("data_boundary", "leak", "privacy_boundary"),
        ("policy", "unsafe", "safety_policy"),
        ("llm_judge", "unclear", "response_quality"),
        ("tool_trace", "gọi tool cấm ['admin']", "forbidden_tool"),
        ("tool_trace", "approval expected=True, actual=False", "approval_mismatch"),
        ("tool_trace", "đã thực thi 2/1 tool call", "tool_call_limit"),
    ],
)
def test_classifies_failure_from_critical_criterion(
    evaluator: str,
    reason: str,
    expected: str,
) -> None:
    result = Result(
        category="safety",
        status="failed",
        critical_passed=False,
        criterion_results=[
            {
                "evaluator": evaluator,
                "passed": False,
                "critical": True,
                "reason": reason,
            }
        ],
    )

    assert classify_failure(result) == expected


def test_prefers_critical_failure_over_non_critical_failure() -> None:
    result = Result(
        category="safety",
        status="failed",
        criterion_results=[
            {
                "evaluator": "llm_judge",
                "passed": False,
                "critical": False,
                "reason": "style",
            },
            {
                "evaluator": "policy",
                "passed": False,
                "critical": True,
                "reason": "unsafe",
            },
        ],
    )

    assert classify_failure(result) == "safety_policy"


def test_empty_metrics_have_no_misleading_rates() -> None:
    metrics = calculate_eval_metrics([], total_expected=4)

    assert metrics.pass_rate is None
    assert metrics.tool_success_rate is None
    assert metrics.missing_cases == 4
    assert metrics.failure_categories == {}


def test_rejects_expected_total_smaller_than_results() -> None:
    with pytest.raises(ValueError, match="total_expected"):
        calculate_eval_metrics([Result(category="chat", status="passed")], total_expected=0)
