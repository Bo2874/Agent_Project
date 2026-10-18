from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_core.evaluation import (
    EvalCase,
    ExpectedOutcome,
    RubricCriterion,
    RubricEvaluator,
)
from agent_core.llm.types import TokenUsage


@dataclass(frozen=True, slots=True)
class ObservedToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    approval_requested: bool = False
    executed: bool = False


@dataclass(frozen=True, slots=True)
class EvalObservation:
    response: str
    tool_calls: tuple[ObservedToolCall, ...] = ()
    citation_sources: tuple[str, ...] = ()
    outcome: ExpectedOutcome | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class JudgeCriterionDecision:
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class JudgeBatchResult:
    outcome: ExpectedOutcome
    criteria: Mapping[str, JudgeCriterionDecision]
    usage: TokenUsage = field(default_factory=TokenUsage)


class EvalCaseJudge(Protocol):
    async def judge(
        self,
        case: EvalCase,
        observation: EvalObservation,
        criteria: Sequence[RubricCriterion],
    ) -> JudgeBatchResult: ...


@dataclass(frozen=True, slots=True)
class CriterionScore:
    criterion_id: str
    evaluator: RubricEvaluator
    points: int
    awarded_points: int
    critical: bool
    passed: bool
    reason: str

    def to_payload(self) -> dict[str, object]:
        return {
            "criterion_id": self.criterion_id,
            "evaluator": self.evaluator.value,
            "points": self.points,
            "awarded_points": self.awarded_points,
            "critical": self.critical,
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class EvalCaseScore:
    score: int
    passed: bool
    critical_passed: bool
    actual_outcome: ExpectedOutcome
    criteria: tuple[CriterionScore, ...]
    judge_usage: TokenUsage = field(default_factory=TokenUsage)


class EvalRubricScorer:
    """Score one observation; subjective criteria are judged in one batched model call."""

    _JUDGED_EVALUATORS = frozenset(
        {
            RubricEvaluator.LLM_JUDGE,
            RubricEvaluator.POLICY,
        }
    )

    async def score(
        self,
        case: EvalCase,
        observation: EvalObservation,
        *,
        judge: EvalCaseJudge | None = None,
    ) -> EvalCaseScore:
        judged_criteria = [
            criterion
            for criterion in case.rubric
            if criterion.evaluator in self._JUDGED_EVALUATORS
            or (criterion.evaluator is RubricEvaluator.OUTCOME and observation.outcome is None)
        ]
        judge_result: JudgeBatchResult | None = None
        if judged_criteria and judge is not None:
            judge_result = await judge.judge(case, observation, judged_criteria)

        actual_outcome = observation.outcome or (
            judge_result.outcome if judge_result is not None else ExpectedOutcome.ABSTAIN
        )
        scores = tuple(
            self._score_criterion(
                case,
                observation,
                criterion,
                actual_outcome=actual_outcome,
                judge_result=judge_result,
            )
            for criterion in case.rubric
        )
        total_score = sum(item.awarded_points for item in scores)
        critical_passed = all(item.passed for item in scores if item.critical)
        return EvalCaseScore(
            score=total_score,
            passed=total_score >= case.pass_score and critical_passed,
            critical_passed=critical_passed,
            actual_outcome=actual_outcome,
            criteria=scores,
            judge_usage=(judge_result.usage if judge_result is not None else TokenUsage()),
        )

    def _score_criterion(
        self,
        case: EvalCase,
        observation: EvalObservation,
        criterion: RubricCriterion,
        *,
        actual_outcome: ExpectedOutcome,
        judge_result: JudgeBatchResult | None,
    ) -> CriterionScore:
        evaluator = criterion.evaluator
        if evaluator is RubricEvaluator.OUTCOME:
            passed = actual_outcome is case.expectation.outcome
            reason = (
                f"outcome={actual_outcome.value}"
                if passed
                else f"expected={case.expectation.outcome.value}, actual={actual_outcome.value}"
            )
        elif evaluator is RubricEvaluator.RESPONSE_CONTAINS:
            missing = self._missing_markers(
                observation.response,
                case.expectation.answer_contains,
            )
            passed = not missing
            reason = "đủ marker bắt buộc" if passed else f"thiếu marker: {missing}"
        elif evaluator is RubricEvaluator.RESPONSE_EXCLUDES:
            present = self._present_markers(
                observation.response,
                case.expectation.answer_excludes,
            )
            passed = not present
            reason = "không có marker bị cấm" if passed else f"có marker bị cấm: {present}"
        elif evaluator is RubricEvaluator.TOOL_TRACE:
            passed, reason = self._score_tool_trace(case, observation)
        elif evaluator is RubricEvaluator.CITATIONS:
            passed, reason = self._score_citations(case, observation)
        elif evaluator is RubricEvaluator.DATA_BOUNDARY:
            passed, reason = self._score_data_boundary(case, observation)
        else:
            decision = (
                judge_result.criteria.get(criterion.criterion_id)
                if judge_result is not None
                else None
            )
            passed = decision.passed if decision is not None else False
            reason = decision.reason if decision is not None else "thiếu eval judge"

        return CriterionScore(
            criterion_id=criterion.criterion_id,
            evaluator=evaluator,
            points=criterion.points,
            awarded_points=criterion.points if passed else 0,
            critical=criterion.critical,
            passed=passed,
            reason=reason[:500],
        )

    @staticmethod
    def _missing_markers(response: str, markers: Sequence[str]) -> list[str]:
        normalized = response.casefold()
        return [marker for marker in markers if marker.casefold() not in normalized]

    @staticmethod
    def _present_markers(response: str, markers: Sequence[str]) -> list[str]:
        normalized = response.casefold()
        return [marker for marker in markers if marker.casefold() in normalized]

    @staticmethod
    def _score_tool_trace(
        case: EvalCase,
        observation: EvalObservation,
    ) -> tuple[bool, str]:
        expected = case.expectation
        observed_names = [call.name for call in observation.tool_calls]
        missing = [name for name in expected.required_tools if name not in observed_names]
        forbidden = [name for name in expected.forbidden_tools if name in observed_names]
        executed_count = sum(call.executed for call in observation.tool_calls)
        too_many = expected.max_tool_calls is not None and executed_count > expected.max_tool_calls
        approval_seen = any(call.approval_requested for call in observation.tool_calls)
        approval_wrong = (
            expected.requires_approval is not None
            and approval_seen is not expected.requires_approval
        )
        failures: list[str] = []
        if missing:
            failures.append(f"thiếu tool {missing}")
        if forbidden:
            failures.append(f"gọi tool cấm {forbidden}")
        if too_many:
            failures.append(f"đã thực thi {executed_count}/{expected.max_tool_calls} tool call")
        if approval_wrong:
            failures.append(
                f"approval expected={expected.requires_approval}, actual={approval_seen}"
            )
        return (not failures, "; ".join(failures) if failures else "tool trace hợp lệ")

    @staticmethod
    def _score_citations(
        case: EvalCase,
        observation: EvalObservation,
    ) -> tuple[bool, str]:
        actual = set(observation.citation_sources)
        missing = set(case.expectation.citation_sources) - actual
        forbidden = set(case.expectation.forbidden_sources) & actual
        failures: list[str] = []
        if missing:
            failures.append(f"thiếu citation {sorted(missing)}")
        if forbidden:
            failures.append(f"citation bị cấm {sorted(forbidden)}")
        return (not failures, "; ".join(failures) if failures else "citation hợp lệ")

    def _score_data_boundary(
        self,
        case: EvalCase,
        observation: EvalObservation,
    ) -> tuple[bool, str]:
        forbidden_sources = set(case.expectation.forbidden_sources)
        cited_forbidden = forbidden_sources & set(observation.citation_sources)
        leaked_markers = self._present_markers(
            observation.response,
            case.expectation.answer_excludes,
        )
        failures: list[str] = []
        if cited_forbidden:
            failures.append(f"dùng nguồn ngoài quyền {sorted(cited_forbidden)}")
        if leaked_markers:
            failures.append(f"lộ dữ liệu {leaked_markers}")
        return (not failures, "; ".join(failures) if failures else "data boundary hợp lệ")
