from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend.dependencies import get_db_session, get_optional_user
from backend.main import app
from backend.models import EvalCaseResult, EvalRun
from backend.routes import admin as admin_routes
from backend.schemas.admin import AdminEvalRunsResponse
from backend.services.admin_evaluations import (
    build_eval_comparison,
    build_eval_run_summary,
    list_admin_eval_runs,
)


def make_run(
    *,
    run_id: UUID | None = None,
    prompt_version: str = "prompt-v1",
    dataset_version: str = "1.0.0",
    dataset_fingerprint: str = "a" * 64,
    status: str = "completed",
    created_at: datetime | None = None,
) -> EvalRun:
    timestamp = created_at or datetime.now(UTC)
    return EvalRun(
        id=run_id or uuid4(),
        dataset_id="personal_agent_core",
        dataset_version=dataset_version,
        dataset_fingerprint=dataset_fingerprint,
        provider="deepseek",
        model_name="deepseek-chat",
        prompt_version=prompt_version,
        prompt_fingerprint="b" * 64,
        judge_provider="deepseek",
        judge_model_name="deepseek-chat",
        judge_prompt_version="eval-judge-v1",
        judge_prompt_fingerprint="c" * 64,
        status=status,
        total_cases=2,
        completed_cases=2,
        selected_case_ids=["chat_case", "tool_case"],
        created_at=timestamp,
        finished_at=timestamp,
    )


def make_result(
    run_id: UUID,
    *,
    case_id: str,
    category: str,
    status: str,
    tool_success: bool | None = None,
) -> EvalCaseResult:
    tool_calls: list[dict[str, Any]] = []
    if tool_success is not None:
        tool_calls.append(
            {
                "name": "demo__calculate",
                "executed": True,
                "error_code": None if tool_success else "tool_execution_failed",
            }
        )
    criterion_results: list[dict[str, Any]] = []
    if status == "failed":
        criterion_results.append(
            {
                "evaluator": "response_contains",
                "passed": False,
                "critical": True,
                "reason": "thiếu marker",
            }
        )
    return EvalCaseResult(
        id=uuid4(),
        eval_run_id=run_id,
        case_id=case_id,
        category=category,
        status=status,
        score=100 if status == "passed" else 20,
        critical_passed=status == "passed",
        tool_calls=tool_calls,
        citation_sources=[],
        criterion_results=criterion_results,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        judge_tokens=3,
        latency_ms=100,
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class FakeSession:
    def __init__(self, responses: list[list[object]]) -> None:
        self.responses = responses

    async def scalars(self, _: object) -> ScalarRows:
        return ScalarRows(self.responses.pop(0))


def test_builds_eval_summary_with_pass_rate_by_category() -> None:
    run = make_run()
    results = [
        make_result(run.id, case_id="chat_case", category="chat", status="passed"),
        make_result(
            run.id,
            case_id="tool_case",
            category="tool",
            status="failed",
            tool_success=False,
        ),
    ]

    summary = build_eval_run_summary(run, results)

    assert summary.pass_rate == 0.5
    assert summary.tool_success_rate == 0.0
    assert summary.categories["chat"].pass_rate == 1.0
    assert summary.categories["tool"].pass_rate == 0.0
    assert summary.failure_categories == {"missing_expected_content": 1}


def test_comparison_calculates_candidate_minus_baseline() -> None:
    baseline_run = make_run(prompt_version="prompt-v1")
    candidate_run = make_run(prompt_version="prompt-v2")
    baseline = build_eval_run_summary(
        baseline_run,
        [
            make_result(
                baseline_run.id,
                case_id="chat_case",
                category="chat",
                status="passed",
            ),
            make_result(
                baseline_run.id,
                case_id="tool_case",
                category="tool",
                status="failed",
                tool_success=False,
            ),
        ],
    )
    candidate = build_eval_run_summary(
        candidate_run,
        [
            make_result(
                candidate_run.id,
                case_id="chat_case",
                category="chat",
                status="passed",
            ),
            make_result(
                candidate_run.id,
                case_id="tool_case",
                category="tool",
                status="passed",
                tool_success=True,
            ),
        ],
    )

    comparison = build_eval_comparison(baseline, candidate)

    assert comparison.same_dataset is True
    assert comparison.same_case_selection is True
    assert comparison.comparable is True
    assert comparison.warnings == []
    assert comparison.delta.pass_rate == 0.5
    assert comparison.delta.tool_success_rate == 1.0
    assert comparison.delta.categories["tool"] == 1.0
    assert comparison.delta.failure_categories == {"missing_expected_content": -1}


def test_comparison_warns_for_different_dataset_and_incomplete_run() -> None:
    baseline = build_eval_run_summary(make_run(), [])
    candidate = build_eval_run_summary(
        make_run(dataset_version="2.0.0", dataset_fingerprint="d" * 64, status="running"),
        [],
    )

    comparison = build_eval_comparison(baseline, candidate)

    assert comparison.same_dataset is False
    assert comparison.comparable is False
    assert len(comparison.warnings) == 2
    assert comparison.delta.pass_rate is None


def test_comparison_warns_when_case_selection_is_different() -> None:
    baseline_run = make_run()
    candidate_run = make_run()
    candidate_run.selected_case_ids = ["chat_case"]

    comparison = build_eval_comparison(
        build_eval_run_summary(baseline_run, []),
        build_eval_run_summary(candidate_run, []),
    )

    assert comparison.same_dataset is True
    assert comparison.same_case_selection is False
    assert comparison.comparable is False
    assert any("case" in warning.lower() for warning in comparison.warnings)


async def test_lists_eval_runs_with_results_grouped_by_run() -> None:
    first = make_run(created_at=datetime(2026, 9, 29, 12, tzinfo=UTC))
    second = make_run(created_at=datetime(2026, 9, 28, 12, tzinfo=UTC))
    results = [
        make_result(first.id, case_id="chat_case", category="chat", status="passed"),
        make_result(second.id, case_id="chat_case", category="chat", status="failed"),
    ]
    session = FakeSession([[first, second], results])

    response = await list_admin_eval_runs(session, limit=20)  # type: ignore[arg-type]

    assert [item.id for item in response.runs] == [first.id, second.id]
    assert response.runs[0].pass_rate == 1.0
    assert response.runs[1].pass_rate == 0.0


async def admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), role="admin", is_active=True)


async def fake_session_dependency() -> object:
    yield object()


async def test_admin_eval_api_returns_versioned_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    run = make_run()
    summary = build_eval_run_summary(run, [])

    async def fake_list(_: object, *, limit: int) -> AdminEvalRunsResponse:
        assert limit == 10
        return AdminEvalRunsResponse(runs=[summary])

    app.dependency_overrides[get_optional_user] = admin_user
    app.dependency_overrides[get_db_session] = fake_session_dependency
    monkeypatch.setattr(admin_routes, "list_admin_eval_runs", fake_list)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/admin/evals?limit=10")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["runs"][0]["prompt_version"] == "prompt-v1"


async def test_compare_api_rejects_same_run_id() -> None:
    run_id = uuid4()
    app.dependency_overrides[get_optional_user] = admin_user
    app.dependency_overrides[get_db_session] = fake_session_dependency
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/admin/evals/compare",
                params={"baseline_id": str(run_id), "candidate_id": str(run_id)},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == "Baseline và candidate phải khác nhau"
