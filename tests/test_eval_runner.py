from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from agent_core.eval_runner import (
    EvalObservation,
    EvalRubricScorer,
    JudgeBatchResult,
    JudgeCriterionDecision,
    ObservedToolCall,
)
from agent_core.evaluation import (
    EvalCase,
    ExpectedOutcome,
    RubricCriterion,
    fingerprint_eval_dataset,
    load_eval_dataset,
)
from agent_core.llm.types import LLMChunk, ModelMessage, TokenUsage
from agent_core.tools import ToolRegistry
from backend.services.evaluation import AgentEvalExecutor, ModelEvalJudge

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PassingJudge:
    def __init__(self, outcome: ExpectedOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def judge(
        self,
        case: EvalCase,
        observation: EvalObservation,
        criteria: Sequence[RubricCriterion],
    ) -> JudgeBatchResult:
        del observation
        self.calls.append((case.case_id, tuple(item.criterion_id for item in criteria)))
        return JudgeBatchResult(
            outcome=self.outcome,
            criteria={item.criterion_id: JudgeCriterionDecision(True, "đạt") for item in criteria},
            usage=TokenUsage(total_tokens=9),
        )


class FakeModel:
    provider = "fake"
    model = "fake-v1"

    def __init__(self, responses: list[list[LLMChunk]]) -> None:
        self.responses = responses
        self.requests: list[list[ModelMessage]] = []

    async def stream(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        del tools
        assert user_id
        self.requests.append(messages)
        for item in self.responses[len(self.requests) - 1]:
            yield item

    async def aclose(self) -> None:
        return None


def get_case(case_id: str) -> EvalCase:
    return next(case for case in load_eval_dataset().cases if case.case_id == case_id)


def test_dataset_fingerprint_is_stable_and_content_addressed() -> None:
    first = load_eval_dataset()
    second = load_eval_dataset(PROJECT_ROOT / "evals/datasets/core_v1.json")

    assert fingerprint_eval_dataset(first) == fingerprint_eval_dataset(second)
    assert len(fingerprint_eval_dataset(first)) == 64


async def test_deterministic_rubric_scores_a_complete_chat_case() -> None:
    case = get_case("chat_recall_conversation_context")
    observation = EvalObservation(
        response="Bạn đang dùng Python.",
        outcome=ExpectedOutcome.ANSWER,
    )

    result = await EvalRubricScorer().score(case, observation)

    assert result.score == 100
    assert result.passed is True
    assert result.critical_passed is True


async def test_critical_failure_forces_case_to_fail() -> None:
    case = get_case("chat_recall_conversation_context")
    observation = EvalObservation(
        response="Bạn đang dùng Java.",
        outcome=ExpectedOutcome.ANSWER,
    )

    result = await EvalRubricScorer().score(case, observation)

    assert result.score == 20
    assert result.passed is False
    assert result.critical_passed is False


async def test_subjective_criteria_are_batched_into_one_judge_call() -> None:
    case = get_case("chat_clarify_ambiguous_delete")
    judge = PassingJudge(ExpectedOutcome.CLARIFY)
    observation = EvalObservation(response="Bạn muốn xóa đối tượng nào?")

    result = await EvalRubricScorer().score(case, observation, judge=judge)

    assert result.score == 100
    assert result.passed is True
    assert result.judge_usage.total_tokens == 9
    assert len(judge.calls) == 1
    assert set(judge.calls[0][1]) == {"clarification", "clear_language"}


async def test_missing_judge_does_not_silently_pass_subjective_criteria() -> None:
    case = get_case("chat_explain_cache_concisely")
    observation = EvalObservation(
        response="Cache lưu tạm dữ liệu để trả lời nhanh hơn.",
        outcome=ExpectedOutcome.ANSWER,
    )

    result = await EvalRubricScorer().score(case, observation)

    assert result.score == 25
    assert result.passed is False
    assert result.critical_passed is False


async def test_approval_request_counts_as_required_tool_but_not_execution() -> None:
    case = get_case("tool_sensitive_requires_approval")
    judge = PassingJudge(ExpectedOutcome.CLARIFY)
    observation = EvalObservation(
        response="Tôi cần bạn phê duyệt trước khi chạy.",
        outcome=ExpectedOutcome.CLARIFY,
        tool_calls=(
            ObservedToolCall(
                name="demo__simulate_sensitive_action",
                approval_requested=True,
                error_code="tool_approval_denied",
                executed=False,
            ),
        ),
    )

    result = await EvalRubricScorer().score(case, observation, judge=judge)

    assert result.score == 100
    assert result.passed is True


async def test_model_judge_parses_one_structured_response() -> None:
    case = get_case("chat_clarify_ambiguous_delete")
    model = FakeModel(
        [
            [
                LLMChunk(
                    content=(
                        '{"outcome":"clarify","criteria":{'
                        '"clarification":{"passed":true,"reason":"đã hỏi lại"}}}'
                    ),
                    usage=TokenUsage(total_tokens=12),
                    finish_reason="stop",
                )
            ]
        ]
    )
    criterion = next(item for item in case.rubric if item.criterion_id == "clarification")

    result = await ModelEvalJudge(model).judge(
        case,
        EvalObservation(response="Bạn muốn xóa gì?"),
        [criterion],
    )

    assert result.outcome is ExpectedOutcome.CLARIFY
    assert result.criteria["clarification"].passed is True
    assert result.usage.total_tokens == 12
    assert len(model.requests) == 1


async def test_agent_eval_executor_collects_response_usage_and_latency() -> None:
    case = get_case("chat_recall_conversation_context")
    model = FakeModel(
        [
            [
                LLMChunk(content="Bạn dùng "),
                LLMChunk(
                    content="Python.",
                    usage=TokenUsage(prompt_tokens=10, completion_tokens=3, total_tokens=13),
                    finish_reason="stop",
                ),
            ]
        ]
    )
    executor = AgentEvalExecutor(model=model, registry=ToolRegistry())

    observation = await executor.execute(case)

    assert observation.response == "Bạn dùng Python."
    assert observation.usage.total_tokens == 13
    assert observation.latency_ms >= 0
    assert observation.tool_calls == ()
