from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from agent_core.evaluation import EvalCase, EvalDataset, fingerprint_eval_dataset
from agent_core.llm.types import ChatModel
from agent_core.prompts import SYSTEM_PROMPT_FINGERPRINT, SYSTEM_PROMPT_VERSION
from backend.core.config import get_settings
from backend.core.redaction import redact_text
from backend.models import EvalCaseResult
from backend.services.evaluation import (
    EVAL_JUDGE_PROMPT_FINGERPRINT,
    EVAL_JUDGE_PROMPT_VERSION,
)
from backend.services.langsmith_tracing import (
    anonymous_trace_id,
    get_langsmith_tracing,
)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]+")
_DATASET_SCHEMA_VERSION = "personal-agent-eval-v1"


@dataclass(slots=True)
class LangSmithExperimentHandle:
    name: str
    project_id: UUID | str
    dataset_id: UUID | str
    example_ids: dict[str, UUID | str]
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    recorded_cases: int = 0
    passed_cases: int = 0
    error_cases: int = 0
    score_total: int = 0


class LangSmithEvaluationService:
    """Mirror the local versioned eval suite into a LangSmith experiment."""

    def __init__(
        self,
        *,
        client: Any | None,
        project_prefix: str,
        hash_salt: str,
    ) -> None:
        self._client = client
        self._project_prefix = self._safe_name(project_prefix)
        self._hash_salt = hash_salt
        self.last_experiment_name: str | None = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def start_experiment(
        self,
        *,
        dataset: EvalDataset,
        cases: tuple[EvalCase, ...],
        model: ChatModel,
        local_run_id: UUID,
    ) -> LangSmithExperimentHandle | None:
        if self._client is None:
            return None
        dataset_id, example_ids = await asyncio.to_thread(self._sync_dataset, dataset)
        correlation = anonymous_trace_id(
            local_run_id,
            salt=self._hash_salt,
            namespace="eval-run",
        )
        experiment_name = self._safe_name(
            f"{self._project_prefix}-eval-{dataset.dataset_id}-v{dataset.version}-"
            f"{model.model}-{SYSTEM_PROMPT_VERSION}-{correlation}"
        )
        evaluator_keys = sorted(
            {
                "overall_pass",
                "rubric_score",
                "critical_passed",
                *(
                    f"rubric.{criterion.criterion_id}"
                    for case in cases
                    for criterion in case.rubric
                ),
            }
        )
        project = await asyncio.to_thread(
            self._client.create_project,
            experiment_name,
            description=(
                f"Personal AI Agent eval {dataset.dataset_id}@{dataset.version}; "
                f"model={model.model}; prompt={SYSTEM_PROMPT_VERSION}."
            ),
            metadata={
                "dataset_id": dataset.dataset_id,
                "dataset_version": dataset.version,
                "dataset_fingerprint": fingerprint_eval_dataset(dataset),
                "provider": model.provider,
                "model": model.model,
                "prompt_version": SYSTEM_PROMPT_VERSION,
                "prompt_fingerprint": SYSTEM_PROMPT_FINGERPRINT,
                "judge_prompt_version": EVAL_JUDGE_PROMPT_VERSION,
                "judge_prompt_fingerprint": EVAL_JUDGE_PROMPT_FINGERPRINT,
                "local_run": correlation,
            },
            reference_dataset_id=dataset_id,
            num_examples=len(cases),
            num_repetitions=1,
            evaluator_keys=evaluator_keys,
        )
        handle = LangSmithExperimentHandle(
            name=experiment_name,
            project_id=project.id,
            dataset_id=dataset_id,
            example_ids={case.case_id: example_ids[case.case_id] for case in cases},
        )
        self.last_experiment_name = experiment_name
        return handle

    async def record_case(
        self,
        handle: LangSmithExperimentHandle,
        *,
        case: EvalCase,
        result: EvalCaseResult,
    ) -> None:
        if self._client is None:
            return
        handle.recorded_cases += 1
        handle.passed_cases += int(result.status == "passed")
        handle.error_cases += int(result.status == "error")
        handle.score_total += result.score or 0
        await asyncio.to_thread(self._record_case, handle, case, result)

    async def finish_experiment(
        self,
        handle: LangSmithExperimentHandle,
        *,
        status: str,
    ) -> None:
        if self._client is None:
            return
        await asyncio.to_thread(self._finish_experiment, handle, status)

    def _sync_dataset(self, dataset: EvalDataset) -> tuple[UUID | str, dict[str, UUID | str]]:
        assert self._client is not None
        name = self._dataset_name(dataset)
        fingerprint = fingerprint_eval_dataset(dataset)
        if self._client.has_dataset(dataset_name=name):
            remote = self._client.read_dataset(dataset_name=name)
            remote_fingerprint = (remote.metadata or {}).get("fingerprint")
            if remote_fingerprint and remote_fingerprint != fingerprint:
                raise ValueError(
                    "Dataset LangSmith đã có cùng version nhưng fingerprint khác; "
                    "hãy tăng version dataset trước khi đồng bộ."
                )
        else:
            remote = self._client.create_dataset(
                name,
                description=dataset.description,
                metadata={
                    "dataset_id": dataset.dataset_id,
                    "version": dataset.version,
                    "fingerprint": fingerprint,
                    "language": dataset.language,
                    "schema_version": _DATASET_SCHEMA_VERSION,
                    "data_classification": "synthetic_eval",
                },
            )

        existing = list(self._client.list_examples(dataset_id=remote.id))
        example_ids = {
            str(item.metadata.get("case_id")): item.id
            for item in existing
            if item.metadata and item.metadata.get("case_id")
        }
        missing = [case for case in dataset.cases if case.case_id not in example_ids]
        if missing:
            examples = [self._example_payload(dataset=dataset, case=case) for case in missing]
            self._client.create_examples(dataset_id=remote.id, examples=examples)
            example_ids.update({case.case_id: self._example_id(dataset, case) for case in missing})
        absent = {case.case_id for case in dataset.cases} - set(example_ids)
        if absent:  # pragma: no cover - defensive check for an invalid client response
            raise RuntimeError(f"LangSmith thiếu eval example: {sorted(absent)}")
        return remote.id, example_ids

    def _record_case(
        self,
        handle: LangSmithExperimentHandle,
        case: EvalCase,
        result: EvalCaseResult,
    ) -> None:
        assert self._client is not None
        langsmith_run_id = uuid4()
        now = datetime.now(UTC)
        started_at = result.created_at or now
        finished_at = result.finished_at or now
        tool_names = [
            str(item.get("name")) for item in (result.tool_calls or []) if item.get("name")
        ]
        self._client.create_run(
            "personal_agent_eval_case",
            {"case_id": case.case_id},
            "chain",
            id=langsmith_run_id,
            trace_id=langsmith_run_id,
            project_name=handle.name,
            start_time=started_at,
            end_time=finished_at,
            reference_example_id=handle.example_ids[case.case_id],
            outputs={
                "status": result.status,
                "score": result.score,
                "critical_passed": result.critical_passed,
                "actual_outcome": result.actual_outcome,
                "tool_names": tool_names,
                "citation_sources": list(result.citation_sources or []),
            },
            error=(
                redact_text(result.error_message, max_length=500) if result.error_message else None
            ),
            tags=[f"category:{case.category.value}", f"dataset:{case.case_id}"],
            extra={
                "metadata": {
                    "case_id": case.case_id,
                    "category": case.category.value,
                    "prompt_version": SYSTEM_PROMPT_VERSION,
                    "dataset_version": self._dataset_version_from_name(handle.name),
                    "payload_policy": "synthetic_dataset_and_aggregate_outputs_only",
                }
            },
        )
        self._feedback(
            handle,
            langsmith_run_id,
            key="overall_pass",
            score=result.status == "passed",
            comment=f"status={result.status}",
            start_time=started_at,
        )
        self._feedback(
            handle,
            langsmith_run_id,
            key="rubric_score",
            score=((result.score or 0) / 100),
            comment=f"{result.score or 0}/100; pass threshold={case.pass_score}",
            start_time=started_at,
        )
        if result.critical_passed is not None:
            self._feedback(
                handle,
                langsmith_run_id,
                key="critical_passed",
                score=result.critical_passed,
                start_time=started_at,
            )
        for criterion in result.criterion_results or []:
            criterion_id = criterion.get("criterion_id")
            if not criterion_id:
                continue
            self._feedback(
                handle,
                langsmith_run_id,
                key=f"rubric.{criterion_id}",
                score=bool(criterion.get("passed")),
                comment=redact_text(criterion.get("reason") or "", max_length=500),
                start_time=started_at,
            )

    def _feedback(
        self,
        handle: LangSmithExperimentHandle,
        run_id: UUID,
        *,
        key: str,
        score: bool | float,
        comment: str | None = None,
        start_time: datetime,
    ) -> None:
        assert self._client is not None
        self._client.create_feedback(
            run_id=run_id,
            trace_id=run_id,
            session_id=handle.project_id,
            key=key,
            score=score,
            comment=comment,
            start_time=start_time,
            source_info={"evaluator": "personal_agent_rubric_v1"},
        )

    def _finish_experiment(self, handle: LangSmithExperimentHandle, status: str) -> None:
        assert self._client is not None
        denominator = max(handle.recorded_cases, 1)
        summary = {
            "pass_rate": handle.passed_cases / denominator,
            "mean_score": (handle.score_total / denominator) / 100,
            "error_rate": handle.error_cases / denominator,
        }
        for key, score in summary.items():
            self._client.create_feedback(
                project_id=handle.project_id,
                key=key,
                score=score,
                value={
                    "status": status,
                    "recorded_cases": handle.recorded_cases,
                },
                source_info={"evaluator": "personal_agent_summary_v1"},
            )

    def _example_payload(self, *, dataset: EvalDataset, case: EvalCase) -> dict[str, Any]:
        return {
            "id": self._example_id(dataset, case),
            "inputs": {
                "case_id": case.case_id,
                "messages": [item.model_dump(mode="json") for item in case.messages],
                "artifacts": [item.model_dump(mode="json") for item in case.artifacts],
            },
            "outputs": {
                "expectation": case.expectation.model_dump(mode="json"),
                "rubric": [item.model_dump(mode="json") for item in case.rubric],
                "pass_score": case.pass_score,
            },
            "metadata": {
                "case_id": case.case_id,
                "name": case.name,
                "category": case.category.value,
                "tags": list(case.tags),
                "requires": list(case.requires),
                "dataset_version": dataset.version,
                "schema_version": _DATASET_SCHEMA_VERSION,
                "data_classification": "synthetic_eval",
            },
            "split": case.category.value,
        }

    def _example_id(self, dataset: EvalDataset, case: EvalCase) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"{self._dataset_name(dataset)}:{case.case_id}",
        )

    def _dataset_name(self, dataset: EvalDataset) -> str:
        return self._safe_name(
            f"{self._project_prefix}-eval-{dataset.dataset_id}-v{dataset.version}"
        )

    @staticmethod
    def _safe_name(value: str) -> str:
        return _SAFE_NAME.sub("-", value).strip("-.")[:240]

    @staticmethod
    def _dataset_version_from_name(experiment_name: str) -> str:
        match = re.search(r"-v(\d+\.\d+\.\d+)-", experiment_name)
        return match.group(1) if match else "unknown"


def create_langsmith_evaluation_service() -> LangSmithEvaluationService:
    settings = get_settings()
    tracing = get_langsmith_tracing()
    return LangSmithEvaluationService(
        client=tracing.client,
        project_prefix=settings.langsmith_project,
        hash_salt=settings.auth_secret_key.get_secret_value(),
    )
