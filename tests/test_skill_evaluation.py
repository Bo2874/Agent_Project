from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_core.graph import AgentLoopEvent, stream_agent_graph
from agent_core.llm.types import LLMChunk, ModelMessage
from agent_core.prompts import SYSTEM_PROMPT
from agent_core.skills import (
    DEFAULT_ADHERENCE_CASES,
    DEFAULT_SELECTION_CASES,
    SkillAdherenceCase,
    SkillExecutionTrace,
    SkillRegistry,
    SkillSelectionConfig,
    SkillSelector,
    evaluate_skill_adherence,
    evaluate_skill_selection,
)
from agent_core.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolRoutingPolicy,
    ToolSelectionConfig,
    create_context_tool,
)
from backend.core.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeModel:
    provider = "fake"
    model = "fake-skill-model"

    def __init__(self) -> None:
        self.requests: list[tuple[list[ModelMessage], list[dict[str, Any]] | None]] = []

    async def stream(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        assert user_id is not None
        self.requests.append((deepcopy(messages), deepcopy(tools)))
        yield LLMChunk(content="Đã hoàn thành.", finish_reason="stop")

    async def aclose(self) -> None:
        return None


def sample_skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.discover(PROJECT_ROOT / "skills")
    return registry


def test_default_skill_selection_benchmark_passes() -> None:
    result = evaluate_skill_selection(
        sample_skill_registry(),
        DEFAULT_SELECTION_CASES,
        available_tools=get_settings().tool_allowlist_names,
    )

    assert result.total == 13
    assert result.passed == 13
    assert result.pass_rate == 1.0
    assert result.to_payload()["failures"] == []


def test_selector_rejects_low_confidence_match() -> None:
    selection = SkillSelector(SkillSelectionConfig(minimum_score=20)).select(
        sample_skill_registry(),
        "Đổi 20 độ C sang Fahrenheit",
        available_tools=get_settings().tool_allowlist_names,
    )

    assert selection.selected_name is None
    assert selection.reason == "low_score"


def test_default_skill_adherence_benchmark_passes() -> None:
    result = evaluate_skill_adherence(
        sample_skill_registry(),
        DEFAULT_ADHERENCE_CASES,
    )

    assert result.total == 2
    assert result.passed == 2
    assert result.pass_rate == 1.0


def test_adherence_evaluator_detects_tool_and_output_violations() -> None:
    invalid_case = SkillAdherenceCase(
        case_id="invalid_calculation",
        expected_skill="multi_step_calculation",
        trace=SkillExecutionTrace(
            selected_skill="multi_step_calculation",
            tool_calls=("demo__count_text",) * 7,
            final_response="Kết quả không có kiểm tra và suy luận nội bộ.",
            tool_error_count=1,
        ),
        expected_tool_sequence=("demo__calculate",),
        required_response_markers=("Các bước", "Kết quả", "Kiểm tra"),
        forbidden_response_markers=("suy luận nội bộ",),
    )

    result = evaluate_skill_adherence(sample_skill_registry(), (invalid_case,))

    assert result.pass_rate == 0
    failures = result.cases[0].failures
    assert "wrong_tool_sequence" in failures
    assert "tool_call_limit_exceeded" in failures
    assert "tool_error_not_handled" in failures
    assert any(item.startswith("unexpected_tools=") for item in failures)
    assert any(item.startswith("missing_response_marker=") for item in failures)
    assert any(item.startswith("forbidden_response_marker=") for item in failures)


async def test_agent_loop_injects_selected_skill_and_required_tools() -> None:
    tool_registry = ToolRegistry()

    async def handler(
        _context: ToolContext,
        _arguments: Mapping[str, Any],
    ) -> ToolResult:
        return ToolResult.success("ok")

    for name, keyword in (
        ("demo__calculate", "tính"),
        ("demo__count_text", "đếm"),
        ("unrelated_one", "thời gian"),
        ("unrelated_two", "nhiệt độ"),
        ("unrelated_three", "runtime"),
    ):
        tool_registry.register(
            create_context_tool(
                name=name,
                description=f"Tool {name}",
                args_schema={"type": "object", "properties": {}},
                coroutine=handler,
            ),
            routing=ToolRoutingPolicy(keywords=frozenset({keyword})),
        )
    model = FakeModel()
    events: list[AgentLoopEvent] = []

    async def capture(event: AgentLoopEvent) -> None:
        events.append(event)

    _ = [
        item
        async for item in stream_agent_graph(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Đếm số từ, ký tự rồi tính trung bình cho văn bản này",
                },
            ],
            registry=tool_registry,
            skill_registry=sample_skill_registry(),
            context=ToolContext(
                user_id=uuid4(),
                conversation_id=uuid4(),
                user_role="user",
            ),
            event_handler=capture,
            tool_selection_config=ToolSelectionConfig(
                lazy_load_threshold=2,
                max_loaded_tools=1,
            ),
        )
    ]

    sent_messages, sent_tools = model.requests[0]
    skill_messages = [
        message
        for message in sent_messages
        if str(message.get("content", "")).startswith("SKILL_INSTRUCTIONS")
    ]
    assert len(skill_messages) == 1
    assert "text_statistics_report" in str(skill_messages[0]["content"])
    assert sent_tools is not None
    assert {tool["function"]["name"] for tool in sent_tools} == {
        "demo__calculate",
        "demo__count_text",
    }
    usage = events[0].context_usage
    assert usage is not None
    assert usage.selected_skill == "text_statistics_report"
    assert usage.skill_tokens > 0
    assert usage.tool_selection_mode == "skill"
