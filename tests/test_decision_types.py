"""Pure local validation only; Jev client/protocol mock contract is intentionally deferred."""

import pytest
from pydantic import ValidationError

from agent_core.routing import DecisionOption, DecisionRequest, DecisionResult


def options():
    return (
        DecisionOption(id="chat", description="Answer without a tool"),
        DecisionOption(id="tool_task", description="Use an available tool"),
    )


def test_state_preserves_vietnamese_negation_numbers_and_whitespace():
    state = "  Không gọi tool. Mã hồ sơ 00123, số dư -42.\n"
    request = DecisionRequest(state=state, instructions="Select one option.", options=options())
    assert request.state == state
    assert "00123" not in repr(request)
    assert "Không gọi tool" not in repr(request)


@pytest.mark.parametrize("state", ["", None, {"query": "hello"}, 10])
def test_initial_boundary_requires_nonempty_text_state(state):
    with pytest.raises(ValidationError):
        DecisionRequest(state=state, instructions="Select one option.", options=options())


@pytest.mark.parametrize("ids", [(), ("chat",), ("chat", "chat"), ("chat", " chat ")])
def test_request_rejects_missing_or_duplicate_alternatives(ids):
    with pytest.raises(ValidationError):
        DecisionRequest(
            state="Hello",
            instructions="Select one option.",
            options=tuple(DecisionOption(id=id_, description="Candidate") for id_ in ids),
        )


def test_request_rejects_more_than_64_candidates():
    with pytest.raises(ValidationError):
        DecisionRequest(
            state="Hello",
            instructions="Select one option.",
            options=tuple(
                DecisionOption(id=f"option_{i}", description="Candidate") for i in range(65)
            ),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"selected_id": "not_available"},
        {"selected_id": "tool_task"},
        {"probabilities": {"chat": 0.8, "tool_task": 0.1}},
        {"probabilities": {"chat": float("nan"), "tool_task": 0.2}},
        {"probabilities": {"chat": float("inf"), "tool_task": 0.2}},
        {"probabilities": {"chat": 1.2, "tool_task": -0.2}},
        {"probabilities": {"chat": True, "tool_task": 0.0}},
        {"confidence": 1.2},
    ],
)
def test_result_rejects_inconsistent_or_nonfinite_distribution(change):
    data = {"selected_id": "chat", "probabilities": {"chat": 0.8, "tool_task": 0.2}}
    with pytest.raises(ValidationError):
        DecisionResult.model_validate({**data, **change})


def test_tied_maximum_is_valid_and_confidence_is_not_invented():
    result = DecisionResult(selected_id="chat", probabilities={"chat": 0.5, "tool_task": 0.5})
    assert result.confidence is None
    assert "model_version" not in result.model_dump()
    assert "input_tokens" not in result.model_dump()
