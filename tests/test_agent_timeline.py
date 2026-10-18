from datetime import UTC, datetime
from uuid import uuid4

from backend.schemas.chat import AgentRunTimelineResponse
from backend.services.agent_runs import AgentRunTimeline, RunStepTimeline, ToolCallTimeline


def test_timeline_response_links_answer_and_excludes_tool_payloads() -> None:
    now = datetime.now(UTC)
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    tool = ToolCallTimeline(
        id=uuid4(),
        tool_name="search_documents",
        status="completed",
        duration_ms=12,
        error_code=None,
        approval_decision=None,
        created_at=now,
        finished_at=now,
    )
    step = RunStepTimeline(
        id=uuid4(),
        step_number=1,
        attempt_number=1,
        status="completed",
        summary="Model yêu cầu gọi tool.",
        model_name="deepseek-flash",
        duration_ms=20,
        total_tokens=30,
        cached_input_tokens=12,
        cache_miss_input_tokens=8,
        prompt_cache_hit_rate=0.6,
        prompt_version="personal-agent-system-v1",
        created_at=now,
        finished_at=now,
        tool_calls=[tool],
    )
    timeline = AgentRunTimeline(
        id=uuid4(),
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        status="completed",
        current_step=1,
        resume_count=0,
        checkpoint_revision=2,
        stop_reason="final_answer",
        total_tokens=30,
        cached_input_tokens=12,
        cache_miss_input_tokens=8,
        prompt_cache_hit_rate=0.6,
        prompt_version="personal-agent-system-v1",
        tool_selection_mode="relevant",
        available_tool_count=12,
        loaded_tool_count=3,
        context_total_tokens=120,
        context_summary_tokens=24,
        context_budget_tokens=32_000,
        context_trimmed_messages=2,
        created_at=now,
        finished_at=now,
        steps=[step],
    )

    payload = AgentRunTimelineResponse.model_validate(timeline).model_dump(mode="json")

    assert payload["assistant_message_id"] == str(assistant_message_id)
    assert payload["steps"][0]["tool_calls"][0]["tool_name"] == "search_documents"
    assert payload["context_total_tokens"] == 120
    assert payload["context_summary_tokens"] == 24
    assert payload["context_trimmed_messages"] == 2
    assert payload["prompt_cache_hit_rate"] == 0.6
    assert payload["steps"][0]["prompt_version"] == "personal-agent-system-v1"
    assert payload["checkpoint_revision"] == 2
    assert payload["tool_selection_mode"] == "relevant"
    assert payload["loaded_tool_count"] == 3
    assert "arguments" not in payload["steps"][0]["tool_calls"][0]
    assert "result_content" not in payload["steps"][0]["tool_calls"][0]
    assert "result_data" not in payload["steps"][0]["tool_calls"][0]
