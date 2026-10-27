from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent_core.graph import AgentLoopEvent, AgentQuotaLimitError, ToolCallRequest
from agent_core.llm.types import TokenUsage
from agent_core.tools import ToolResult
from backend.services import usage_limits
from backend.services.usage_limits import AgentQuotaGuard, UsageLimit, _window


async def test_api_tool_tokens_count_once_in_daily_quota_even_if_output_validation_fails(
    monkeypatch,
):
    captured = []

    async def add(user_id, *, metric, amount, **kwargs):
        captured.append((metric, amount))

    monkeypatch.setattr(usage_limits, "add_usage", add)
    guard = AgentQuotaGuard(user_id=uuid4(), token_limit=1000, tool_call_limit=10)
    await guard.record_event(
        AgentLoopEvent(
            kind="tool_completed",
            step_number=1,
            tool_call=ToolCallRequest(id="c1", name="search_web", arguments={}, raw_arguments="{}"),
            tool_result=ToolResult.failure(
                "Missing sources", error_code="web_search_missing_sources"
            ),
            usage=TokenUsage(total_tokens=120),
        )
    )
    assert captured == [("llm_token", 120)]


def test_fixed_window_is_anchored_and_returns_retry_after() -> None:
    start, retry_after = _window(datetime(2026, 9, 28, 12, 0, 59, tzinfo=UTC), 60)

    assert start == datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
    assert retry_after == 1


@pytest.mark.asyncio
async def test_token_quota_blocks_next_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def current_usage(*_: object, **__: object) -> int:
        return 100

    monkeypatch.setattr(usage_limits, "get_current_usage", current_usage)
    guard = AgentQuotaGuard(user_id=uuid4(), token_limit=100, tool_call_limit=10)

    with pytest.raises(AgentQuotaLimitError, match="quota token"):
        await guard.before_execution("model")


@pytest.mark.asyncio
async def test_tool_quota_consumes_one_atomic_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[UsageLimit, ...]] = []

    async def consume(_: object, limits: tuple[UsageLimit, ...]) -> None:
        captured.append(limits)

    monkeypatch.setattr(usage_limits, "consume_usage_limits", consume)
    guard = AgentQuotaGuard(user_id=uuid4(), token_limit=100, tool_call_limit=10)

    await guard.before_execution("tool")

    assert captured[0][0].metric == "tool_call"
    assert captured[0][0].amount == 1


@pytest.mark.asyncio
async def test_model_usage_is_recorded_after_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, int]] = []

    async def add(_: object, *, metric: str, amount: int, **__: object) -> int:
        captured.append((metric, amount))
        return amount

    monkeypatch.setattr(usage_limits, "add_usage", add)
    guard = AgentQuotaGuard(user_id=uuid4(), token_limit=100, tool_call_limit=10)

    await guard.record_event(
        AgentLoopEvent(
            kind="model_completed",
            step_number=1,
            usage=TokenUsage(total_tokens=42),
        )
    )

    assert captured == [("llm_token", 42)]
