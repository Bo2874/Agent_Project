from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_core.evaluation import (
    EvalCategory,
    EvalDataset,
    load_eval_dataset,
    summarize_eval_dataset,
)


def test_core_dataset_loads_all_eval_categories() -> None:
    dataset = load_eval_dataset()

    assert dataset.dataset_id == "personal_agent_core"
    assert dataset.version == "1.0.0"
    assert len(dataset.cases) == 20
    assert {case.category for case in dataset.cases} == set(EvalCategory)

    summary = summarize_eval_dataset(dataset)
    assert summary["categories"] == {
        "chat": 4,
        "rag": 5,
        "safety": 6,
        "tool": 5,
    }


def test_each_case_has_a_complete_rubric_and_unique_id() -> None:
    dataset = load_eval_dataset()

    assert len({case.case_id for case in dataset.cases}) == len(dataset.cases)
    for case in dataset.cases:
        assert sum(criterion.points for criterion in case.rubric) == 100
        assert case.pass_score == 80
        assert len({criterion.criterion_id for criterion in case.rubric}) == len(case.rubric)


def test_safety_cases_have_a_critical_gate() -> None:
    dataset = load_eval_dataset()
    safety_cases = [case for case in dataset.cases if case.category is EvalCategory.SAFETY]

    assert safety_cases
    assert all(any(criterion.critical for criterion in case.rubric) for case in safety_cases)
    assert all(case.expectation.outcome.value in {"answer", "refuse"} for case in safety_cases)


def test_rag_answers_define_sources_and_privacy_case_forbids_cross_user_source() -> None:
    dataset = load_eval_dataset()
    rag_cases = [case for case in dataset.cases if case.category is EvalCategory.RAG]

    for case in rag_cases:
        if case.expectation.outcome.value != "abstain":
            assert case.expectation.citation_sources

    privacy_case = next(
        case for case in rag_cases if case.case_id == "rag_respect_user_data_boundary"
    )
    assert privacy_case.artifacts[0].owner_id == "user-b"
    assert privacy_case.artifacts[0].source_id in privacy_case.expectation.forbidden_sources


def test_prompt_injection_cases_forbid_privileged_actions() -> None:
    dataset = load_eval_dataset()
    injection_cases = [
        case for case in dataset.cases if any("prompt_injection" in tag for tag in case.tags)
    ]

    assert len(injection_cases) >= 3
    assert all(any(criterion.critical for criterion in case.rubric) for case in injection_cases)
    assert any(case.expectation.forbidden_tools for case in injection_cases)


def test_invalid_rubric_total_is_rejected() -> None:
    payload = load_eval_dataset().model_dump(mode="json")
    payload["cases"][0]["rubric"][0]["points"] = 19

    with pytest.raises(ValidationError, match="Rubric points must total 100"):
        EvalDataset.model_validate(payload)


def test_duplicate_case_id_is_rejected() -> None:
    payload = load_eval_dataset().model_dump(mode="json")
    payload["cases"][1]["case_id"] = payload["cases"][0]["case_id"]
    payload["cases"][1]["category"] = payload["cases"][0]["category"]

    with pytest.raises(ValidationError, match="Eval case IDs must be unique"):
        EvalDataset.model_validate(payload)
