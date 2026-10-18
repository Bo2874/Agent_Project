from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.eval_metrics import calculate_eval_metrics
from backend.models import EvalCaseResult, EvalRun
from backend.schemas.admin import (
    AdminEvalCategoryMetrics,
    AdminEvalComparisonDelta,
    AdminEvalComparisonResponse,
    AdminEvalRunsResponse,
    AdminEvalRunSummary,
)


def build_eval_run_summary(
    run: EvalRun,
    results: Sequence[EvalCaseResult],
) -> AdminEvalRunSummary:
    metrics = calculate_eval_metrics(results, total_expected=run.total_cases)
    return AdminEvalRunSummary(
        id=run.id,
        status=run.status,
        dataset_id=run.dataset_id,
        dataset_version=run.dataset_version,
        dataset_fingerprint=run.dataset_fingerprint,
        provider=run.provider,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        prompt_fingerprint=run.prompt_fingerprint,
        judge_model_name=run.judge_model_name,
        selected_case_ids=list(run.selected_case_ids),
        total_cases=metrics.total_expected,
        completed_cases=metrics.total_results,
        passed_cases=metrics.passed_cases,
        failed_cases=metrics.failed_cases,
        error_cases=metrics.error_cases,
        pass_rate=metrics.pass_rate,
        tool_success_rate=metrics.tool_success_rate,
        categories={
            name: AdminEvalCategoryMetrics(
                attempted=item.attempted,
                passed=item.passed,
                failed=item.failed,
                errors=item.errors,
                pass_rate=item.pass_rate,
            )
            for name, item in metrics.categories.items()
        },
        failure_categories=dict(metrics.failure_categories),
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


async def list_admin_eval_runs(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> AdminEvalRunsResponse:
    if not 1 <= limit <= 100:
        raise ValueError("limit phải từ 1 đến 100")
    runs = list(
        (
            await session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit))
        ).all()
    )
    if not runs:
        return AdminEvalRunsResponse(runs=[])
    run_ids = [run.id for run in runs]
    results = list(
        (
            await session.scalars(
                select(EvalCaseResult)
                .where(EvalCaseResult.eval_run_id.in_(run_ids))
                .order_by(EvalCaseResult.created_at, EvalCaseResult.case_id)
            )
        ).all()
    )
    results_by_run: dict[UUID, list[EvalCaseResult]] = {}
    for result in results:
        results_by_run.setdefault(result.eval_run_id, []).append(result)
    return AdminEvalRunsResponse(
        runs=[build_eval_run_summary(run, results_by_run.get(run.id, [])) for run in runs]
    )


async def compare_admin_eval_runs(
    session: AsyncSession,
    *,
    baseline_id: UUID,
    candidate_id: UUID,
) -> AdminEvalComparisonResponse | None:
    if baseline_id == candidate_id:
        raise ValueError("Baseline và candidate phải là hai eval run khác nhau")
    runs = list(
        (
            await session.scalars(
                select(EvalRun).where(EvalRun.id.in_((baseline_id, candidate_id)))
            )
        ).all()
    )
    if len(runs) != 2:
        return None
    run_by_id = {run.id: run for run in runs}
    results = list(
        (
            await session.scalars(
                select(EvalCaseResult)
                .where(EvalCaseResult.eval_run_id.in_((baseline_id, candidate_id)))
                .order_by(EvalCaseResult.created_at, EvalCaseResult.case_id)
            )
        ).all()
    )
    results_by_run: dict[UUID, list[EvalCaseResult]] = {}
    for result in results:
        results_by_run.setdefault(result.eval_run_id, []).append(result)
    baseline = build_eval_run_summary(
        run_by_id[baseline_id],
        results_by_run.get(baseline_id, []),
    )
    candidate = build_eval_run_summary(
        run_by_id[candidate_id],
        results_by_run.get(candidate_id, []),
    )
    return build_eval_comparison(baseline, candidate)


def build_eval_comparison(
    baseline: AdminEvalRunSummary,
    candidate: AdminEvalRunSummary,
) -> AdminEvalComparisonResponse:
    same_dataset = (
        baseline.dataset_id == candidate.dataset_id
        and baseline.dataset_version == candidate.dataset_version
        and baseline.dataset_fingerprint == candidate.dataset_fingerprint
    )
    same_case_selection = set(baseline.selected_case_ids) == set(candidate.selected_case_ids)
    warnings: list[str] = []
    if not same_dataset:
        warnings.append(
            "Hai run không dùng cùng dataset version/fingerprint; "
            "chênh lệch có thể không tương đương."
        )
    if not same_case_selection:
        warnings.append("Hai run không chạy cùng tập case; pass rate không thể so sánh trực tiếp.")
    if baseline.status != "completed" or candidate.status != "completed":
        warnings.append("Có run chưa hoàn thành; pass rate hiện tại có thể thay đổi.")

    category_names = set(baseline.categories) | set(candidate.categories)
    failure_names = set(baseline.failure_categories) | set(candidate.failure_categories)
    return AdminEvalComparisonResponse(
        baseline=baseline,
        candidate=candidate,
        same_dataset=same_dataset,
        same_case_selection=same_case_selection,
        comparable=same_dataset and same_case_selection,
        warnings=warnings,
        delta=AdminEvalComparisonDelta(
            pass_rate=_rate_delta(candidate.pass_rate, baseline.pass_rate),
            tool_success_rate=_rate_delta(
                candidate.tool_success_rate,
                baseline.tool_success_rate,
            ),
            categories={
                name: _rate_delta(
                    candidate.categories.get(name).pass_rate
                    if name in candidate.categories
                    else None,
                    baseline.categories.get(name).pass_rate
                    if name in baseline.categories
                    else None,
                )
                for name in sorted(category_names)
            },
            failure_categories={
                name: candidate.failure_categories.get(name, 0)
                - baseline.failure_categories.get(name, 0)
                for name in sorted(failure_names)
            },
        ),
    )


def _rate_delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline
