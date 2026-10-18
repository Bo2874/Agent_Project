from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from agent_core.evaluation import load_eval_dataset
from backend.services.langsmith_evaluation import LangSmithEvaluationService


class FakeModel:
    provider = "fake-provider"
    model = "fake-model-v2"


class FakeLangSmithClient:
    def __init__(self) -> None:
        self.datasets: dict[str, SimpleNamespace] = {}
        self.examples: dict[UUID | str, list[SimpleNamespace]] = {}
        self.projects: list[SimpleNamespace] = []
        self.runs: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []
        self.created_example_batches = 0

    def has_dataset(self, *, dataset_name: str) -> bool:
        return dataset_name in self.datasets

    def read_dataset(self, *, dataset_name: str) -> SimpleNamespace:
        return self.datasets[dataset_name]

    def create_dataset(
        self,
        name: str,
        *,
        description: str,
        metadata: dict[str, Any],
    ) -> SimpleNamespace:
        dataset = SimpleNamespace(
            id=uuid4(),
            name=name,
            description=description,
            metadata=metadata,
        )
        self.datasets[name] = dataset
        self.examples[dataset.id] = []
        return dataset

    def list_examples(self, *, dataset_id: UUID | str) -> list[SimpleNamespace]:
        return list(self.examples[dataset_id])

    def create_examples(
        self,
        *,
        dataset_id: UUID | str,
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.created_example_batches += 1
        for payload in examples:
            self.examples[dataset_id].append(SimpleNamespace(**payload))
        return {"count": len(examples), "example_ids": [item["id"] for item in examples]}

    def create_project(self, name: str, **kwargs: Any) -> SimpleNamespace:
        project = SimpleNamespace(id=uuid4(), name=name, kwargs=kwargs)
        self.projects.append(project)
        return project

    def create_run(self, name: str, inputs: dict[str, Any], run_type: str, **kwargs: Any) -> None:
        self.runs.append({"name": name, "inputs": inputs, "run_type": run_type, **kwargs})

    def create_feedback(self, **kwargs: Any) -> SimpleNamespace:
        self.feedback.append(kwargs)
        return SimpleNamespace(id=uuid4())


def _service(client: FakeLangSmithClient | None) -> LangSmithEvaluationService:
    return LangSmithEvaluationService(
        client=client,
        project_prefix="personal-ai-agent",
        hash_salt="a-test-secret-with-at-least-32-characters",
    )


async def test_syncs_versioned_dataset_rubric_and_creates_experiment() -> None:
    dataset = load_eval_dataset()
    client = FakeLangSmithClient()
    service = _service(client)

    handle = await service.start_experiment(
        dataset=dataset,
        cases=dataset.cases[:2],
        model=FakeModel(),  # type: ignore[arg-type]
        local_run_id=uuid4(),
    )

    assert handle is not None
    assert len(client.datasets) == 1
    assert len(client.examples[handle.dataset_id]) == len(dataset.cases)
    first = client.examples[handle.dataset_id][0]
    assert first.inputs["case_id"] == dataset.cases[0].case_id
    assert first.outputs["rubric"] == [
        criterion.model_dump(mode="json") for criterion in dataset.cases[0].rubric
    ]
    assert first.outputs["pass_score"] == dataset.cases[0].pass_score
    assert first.metadata["data_classification"] == "synthetic_eval"
    assert client.projects[0].kwargs["reference_dataset_id"] == handle.dataset_id
    assert client.projects[0].kwargs["metadata"]["model"] == "fake-model-v2"
    assert str(handle.project_id) not in handle.name

    await service.start_experiment(
        dataset=dataset,
        cases=dataset.cases[:1],
        model=FakeModel(),  # type: ignore[arg-type]
        local_run_id=uuid4(),
    )
    assert client.created_example_batches == 1


async def test_records_case_feedback_and_experiment_summary_without_raw_response() -> None:
    dataset = load_eval_dataset()
    case = dataset.cases[0]
    client = FakeLangSmithClient()
    service = _service(client)
    handle = await service.start_experiment(
        dataset=dataset,
        cases=(case,),
        model=FakeModel(),  # type: ignore[arg-type]
        local_run_id=uuid4(),
    )
    assert handle is not None
    result = SimpleNamespace(
        status="passed",
        score=90,
        critical_passed=True,
        actual_outcome="answer",
        response="Nội dung người dùng không được export trong experiment output.",
        tool_calls=[{"name": "demo__calculate", "arguments": {"secret": "do-not-export"}}],
        citation_sources=[],
        criterion_results=[
            {
                "criterion_id": "correct_outcome",
                "passed": True,
                "reason": "outcome=answer",
            }
        ],
        error_message=None,
        created_at=None,
        finished_at=None,
    )

    await service.record_case(handle, case=case, result=result)  # type: ignore[arg-type]
    await service.finish_experiment(handle, status="completed")

    assert len(client.runs) == 1
    remote_run = client.runs[0]
    assert remote_run["reference_example_id"] == handle.example_ids[case.case_id]
    assert remote_run["outputs"]["score"] == 90
    assert "response" not in remote_run["outputs"]
    assert "arguments" not in remote_run["outputs"]
    run_feedback = {item["key"]: item for item in client.feedback if "run_id" in item}
    assert run_feedback["overall_pass"]["score"] is True
    assert run_feedback["rubric_score"]["score"] == 0.9
    assert run_feedback["rubric.correct_outcome"]["score"] is True
    project_feedback = {item["key"]: item for item in client.feedback if "project_id" in item}
    assert project_feedback["pass_rate"]["score"] == 1
    assert project_feedback["mean_score"]["score"] == 0.9
    assert project_feedback["error_rate"]["score"] == 0


async def test_disabled_service_is_a_noop() -> None:
    dataset = load_eval_dataset()
    service = _service(None)

    handle = await service.start_experiment(
        dataset=dataset,
        cases=dataset.cases[:1],
        model=FakeModel(),  # type: ignore[arg-type]
        local_run_id=uuid4(),
    )

    assert service.enabled is False
    assert handle is None
    assert service.last_experiment_name is None


async def test_rejects_same_remote_version_with_different_fingerprint() -> None:
    dataset = load_eval_dataset()
    client = FakeLangSmithClient()
    service = _service(client)
    await service.start_experiment(
        dataset=dataset,
        cases=dataset.cases[:1],
        model=FakeModel(),  # type: ignore[arg-type]
        local_run_id=uuid4(),
    )
    remote = next(iter(client.datasets.values()))
    remote.metadata["fingerprint"] = "different"

    with pytest.raises(ValueError, match="fingerprint"):
        await service.start_experiment(
            dataset=dataset,
            cases=dataset.cases[:1],
            model=FakeModel(),  # type: ignore[arg-type]
            local_run_id=uuid4(),
        )
