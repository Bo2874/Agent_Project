from __future__ import annotations

import json
from collections import Counter
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EvalCategory(StrEnum):
    CHAT = "chat"
    TOOL = "tool"
    RAG = "rag"
    SAFETY = "safety"


class ExpectedOutcome(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    ABSTAIN = "abstain"
    REFUSE = "refuse"


class RubricEvaluator(StrEnum):
    OUTCOME = "outcome"
    RESPONSE_CONTAINS = "response_contains"
    RESPONSE_EXCLUDES = "response_excludes"
    TOOL_TRACE = "tool_trace"
    CITATIONS = "citations"
    DATA_BOUNDARY = "data_boundary"
    POLICY = "policy"
    LLM_JUDGE = "llm_judge"


class EvalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1, max_length=20_000)


class EvalArtifact(BaseModel):
    """Untrusted context supplied to an eval case, such as a RAG chunk or tool output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{2,79}$")
    kind: Literal["rag", "memory", "tool_output"]
    content: str = Field(min_length=1, max_length=20_000)
    owner_id: str | None = Field(default=None, max_length=100)
    trusted: bool = False


class EvalExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ExpectedOutcome
    answer_contains: tuple[str, ...] = ()
    answer_excludes: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    citation_sources: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    max_tool_calls: int | None = Field(default=None, ge=0, le=100)
    requires_approval: bool | None = None

    @model_validator(mode="after")
    def validate_disjoint_expectations(self) -> EvalExpectation:
        if set(self.required_tools) & set(self.forbidden_tools):
            raise ValueError("A tool cannot be both required and forbidden")
        if set(self.citation_sources) & set(self.forbidden_sources):
            raise ValueError("A source cannot be both citable and forbidden")
        return self


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    description: str = Field(min_length=3, max_length=500)
    evaluator: RubricEvaluator
    points: int = Field(ge=1, le=100)
    critical: bool = False
    params: dict[str, Any] = Field(default_factory=dict)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^(chat|tool|rag|safety)_[a-z0-9_]{3,70}$")
    category: EvalCategory
    name: str = Field(min_length=3, max_length=200)
    messages: tuple[EvalMessage, ...] = Field(min_length=1, max_length=30)
    artifacts: tuple[EvalArtifact, ...] = Field(default=(), max_length=20)
    expectation: EvalExpectation
    rubric: tuple[RubricCriterion, ...] = Field(min_length=1, max_length=20)
    pass_score: int = Field(default=80, ge=1, le=100)
    tags: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()

    @field_validator("tags", "requires")
    @classmethod
    def validate_unique_text_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Values must be unique")
        return values

    @model_validator(mode="after")
    def validate_case_contract(self) -> EvalCase:
        if not self.case_id.startswith(f"{self.category.value}_"):
            raise ValueError("case_id prefix must match category")
        if sum(item.points for item in self.rubric) != 100:
            raise ValueError("Rubric points must total 100")
        criterion_ids = [item.criterion_id for item in self.rubric]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Rubric criterion IDs must be unique within a case")
        source_ids = [item.source_id for item in self.artifacts]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Artifact source IDs must be unique within a case")
        if self.category is EvalCategory.TOOL and not (
            self.expectation.required_tools or self.expectation.forbidden_tools
        ):
            raise ValueError("Tool cases must define required_tools or forbidden_tools")
        if (
            self.category is EvalCategory.RAG
            and self.expectation.outcome is not ExpectedOutcome.ABSTAIN
            and not self.expectation.citation_sources
        ):
            raise ValueError("Answering RAG cases must define citation_sources")
        if self.category is EvalCategory.SAFETY and not any(item.critical for item in self.rubric):
            raise ValueError("Safety cases must have at least one critical criterion")
        return self


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,79}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=3, max_length=1_000)
    language: str = Field(default="vi", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    cases: tuple[EvalCase, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_dataset_contract(self) -> EvalDataset:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Eval case IDs must be unique")
        present_categories = {case.category for case in self.cases}
        if present_categories != set(EvalCategory):
            raise ValueError("Dataset must contain chat, tool, rag and safety cases")
        return self


DEFAULT_EVAL_DATASET_PATH = (
    Path(__file__).resolve().parent.parent / "evals" / "datasets" / "core_v1.json"
)
MAX_EVAL_DATASET_BYTES = 2 * 1024 * 1024


def load_eval_dataset(path: str | Path = DEFAULT_EVAL_DATASET_PATH) -> EvalDataset:
    dataset_path = Path(path)
    if dataset_path.stat().st_size > MAX_EVAL_DATASET_BYTES:
        raise ValueError(f"Eval dataset exceeds {MAX_EVAL_DATASET_BYTES} bytes")
    with dataset_path.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    return EvalDataset.model_validate(payload)


def summarize_eval_dataset(dataset: EvalDataset) -> dict[str, object]:
    category_counts = Counter(case.category.value for case in dataset.cases)
    return {
        "dataset_id": dataset.dataset_id,
        "version": dataset.version,
        "total_cases": len(dataset.cases),
        "categories": dict(sorted(category_counts.items())),
        "critical_criteria": sum(
            criterion.critical for case in dataset.cases for criterion in case.rubric
        ),
    }


def fingerprint_eval_dataset(dataset: EvalDataset) -> str:
    canonical = json.dumps(
        dataset.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()
