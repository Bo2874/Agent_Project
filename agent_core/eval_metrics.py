from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class EvalResultLike(Protocol):
    category: str
    status: str
    critical_passed: bool | None
    tool_calls: Sequence[Mapping[str, Any]]
    criterion_results: Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class EvalCategoryMetrics:
    total: int
    attempted: int
    passed: int
    failed: int
    errors: int
    skipped: int
    pass_rate: float | None

    def to_payload(self) -> dict[str, int | float | None]:
        return {
            "total": self.total,
            "attempted": self.attempted,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "pass_rate": self.pass_rate,
        }


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    total_expected: int
    total_results: int
    missing_cases: int
    attempted_cases: int
    passed_cases: int
    failed_cases: int
    error_cases: int
    skipped_cases: int
    pass_rate: float | None
    tool_attempts: int
    tool_executions: int
    tool_successes: int
    tool_failures: int
    tool_blocked: int
    tool_success_rate: float | None
    categories: Mapping[str, EvalCategoryMetrics]
    failure_categories: Mapping[str, int]

    def to_payload(self) -> dict[str, object]:
        return {
            "cases": {
                "expected": self.total_expected,
                "results": self.total_results,
                "missing": self.missing_cases,
                "attempted": self.attempted_cases,
                "passed": self.passed_cases,
                "failed": self.failed_cases,
                "errors": self.error_cases,
                "skipped": self.skipped_cases,
                "pass_rate": self.pass_rate,
            },
            "tools": {
                "attempts": self.tool_attempts,
                "executions": self.tool_executions,
                "successes": self.tool_successes,
                "failures": self.tool_failures,
                "blocked": self.tool_blocked,
                "success_rate": self.tool_success_rate,
            },
            "categories": {
                name: metrics.to_payload() for name, metrics in sorted(self.categories.items())
            },
            "failure_categories": dict(sorted(self.failure_categories.items())),
        }


def calculate_eval_metrics(
    results: Sequence[EvalResultLike],
    *,
    total_expected: int | None = None,
) -> EvalMetrics:
    expected = len(results) if total_expected is None else total_expected
    if expected < len(results):
        raise ValueError("total_expected không được nhỏ hơn số result")

    status_counts = Counter(result.status for result in results)
    unknown_statuses = set(status_counts) - {"passed", "failed", "error", "skipped"}
    if unknown_statuses:
        raise ValueError(f"Eval result có status không hợp lệ: {sorted(unknown_statuses)}")

    attempted = status_counts["passed"] + status_counts["failed"] + status_counts["error"]
    category_metrics: dict[str, EvalCategoryMetrics] = {}
    for category in sorted({result.category for result in results}):
        category_results = [result for result in results if result.category == category]
        counts = Counter(result.status for result in category_results)
        category_attempted = counts["passed"] + counts["failed"] + counts["error"]
        category_metrics[category] = EvalCategoryMetrics(
            total=len(category_results),
            attempted=category_attempted,
            passed=counts["passed"],
            failed=counts["failed"],
            errors=counts["error"],
            skipped=counts["skipped"],
            pass_rate=_safe_rate(counts["passed"], category_attempted),
        )

    tool_attempts = 0
    tool_executions = 0
    tool_successes = 0
    tool_failures = 0
    tool_blocked = 0
    for result in results:
        for call in result.tool_calls or ():
            tool_attempts += 1
            if bool(call.get("executed")):
                tool_executions += 1
                if call.get("error_code"):
                    tool_failures += 1
                else:
                    tool_successes += 1
            else:
                tool_blocked += 1

    failures = Counter(
        category for result in results if (category := classify_failure(result)) is not None
    )
    return EvalMetrics(
        total_expected=expected,
        total_results=len(results),
        missing_cases=expected - len(results),
        attempted_cases=attempted,
        passed_cases=status_counts["passed"],
        failed_cases=status_counts["failed"],
        error_cases=status_counts["error"],
        skipped_cases=status_counts["skipped"],
        pass_rate=_safe_rate(status_counts["passed"], attempted),
        tool_attempts=tool_attempts,
        tool_executions=tool_executions,
        tool_successes=tool_successes,
        tool_failures=tool_failures,
        tool_blocked=tool_blocked,
        tool_success_rate=_safe_rate(tool_successes, tool_executions),
        categories=category_metrics,
        failure_categories=dict(failures),
    )


def classify_failure(result: EvalResultLike) -> str | None:
    if result.status == "error":
        return "execution_error"
    if result.status != "failed":
        return None

    failed_criteria = [
        item for item in (result.criterion_results or ()) if item.get("passed") is False
    ]
    if not failed_criteria:
        return "unclassified"
    primary = next(
        (item for item in failed_criteria if bool(item.get("critical"))),
        failed_criteria[0],
    )
    evaluator = str(primary.get("evaluator") or "")
    reason = str(primary.get("reason") or "").casefold()
    if evaluator == "outcome":
        return "wrong_outcome"
    if evaluator == "response_contains":
        return "missing_expected_content"
    if evaluator == "response_excludes":
        return "forbidden_content"
    if evaluator == "citations":
        return "citation_failure"
    if evaluator == "data_boundary":
        return "privacy_boundary"
    if evaluator == "policy":
        return "safety_policy"
    if evaluator == "llm_judge":
        return "response_quality"
    if evaluator == "tool_trace":
        if "thiếu tool" in reason:
            return "missing_tool"
        if "tool cấm" in reason:
            return "forbidden_tool"
        if "approval" in reason:
            return "approval_mismatch"
        if "thực thi" in reason:
            return "tool_call_limit"
        return "tool_trace"
    return "unclassified"


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator
