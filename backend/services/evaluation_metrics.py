from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_core.eval_metrics import EvalMetrics, calculate_eval_metrics
from backend.models import EvalCaseResult, EvalRun


@dataclass(frozen=True, slots=True)
class EvalRunMetricsReport:
    run_id: UUID
    status: str
    dataset_id: str
    dataset_version: str
    dataset_fingerprint: str
    provider: str
    model_name: str
    prompt_version: str
    prompt_fingerprint: str
    judge_model_name: str
    judge_prompt_version: str
    created_at: datetime
    finished_at: datetime | None
    metrics: EvalMetrics

    def to_payload(self) -> dict[str, object]:
        return {
            "run": {
                "id": str(self.run_id),
                "status": self.status,
                "dataset": {
                    "id": self.dataset_id,
                    "version": self.dataset_version,
                    "fingerprint": self.dataset_fingerprint,
                },
                "model": {
                    "provider": self.provider,
                    "name": self.model_name,
                },
                "prompt": {
                    "version": self.prompt_version,
                    "fingerprint": self.prompt_fingerprint,
                },
                "judge": {
                    "model": self.judge_model_name,
                    "prompt_version": self.judge_prompt_version,
                },
                "created_at": self.created_at.isoformat(),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            },
            "metrics": self.metrics.to_payload(),
        }


async def load_eval_run_metrics(
    session: AsyncSession,
    run_id: UUID | None = None,
) -> EvalRunMetricsReport | None:
    if run_id is None:
        run = await session.scalar(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(1))
    else:
        run = await session.get(EvalRun, run_id)
    if run is None:
        return None

    case_results = list(
        (
            await session.scalars(
                select(EvalCaseResult)
                .where(EvalCaseResult.eval_run_id == run.id)
                .order_by(EvalCaseResult.created_at, EvalCaseResult.case_id)
            )
        ).all()
    )
    metrics = calculate_eval_metrics(case_results, total_expected=run.total_cases)
    return EvalRunMetricsReport(
        run_id=run.id,
        status=run.status,
        dataset_id=run.dataset_id,
        dataset_version=run.dataset_version,
        dataset_fingerprint=run.dataset_fingerprint,
        provider=run.provider,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        prompt_fingerprint=run.prompt_fingerprint,
        judge_model_name=run.judge_model_name,
        judge_prompt_version=run.judge_prompt_version,
        created_at=run.created_at,
        finished_at=run.finished_at,
        metrics=metrics,
    )
