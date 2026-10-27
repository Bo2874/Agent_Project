import json

from agent_core.context_budget import ContextTokenUsage
from agent_core.graph import AgentLoopEvent, ToolCallRequest
from agent_core.llm.types import LLMChunk, TokenUsage
from agent_core.status import AgentStatusBlock, AgentStatusBudget
from agent_core.tools import ToolResult
from backend.routes.chat import agent_status_payload, agent_stream_sse, sse_event


def test_tool_status_payload_contains_safe_progress_only() -> None:
    event = AgentLoopEvent(
        kind="tool_completed",
        step_number=2,
        duration_ms=15,
        tool_call=ToolCallRequest(
            id="call_123",
            name="search_documents",
            arguments={"query": "secret argument"},
            raw_arguments='{"query":"secret argument"}',
        ),
        tool_result=ToolResult.success(
            "secret result",
            data={"document": "private data"},
        ),
    )

    payload = agent_status_payload(event)
    serialized = json.dumps(payload)

    assert payload == {
        "status": "tool_completed",
        "step": 2,
        "message": "Tool search_documents đã chạy xong.",
        "duration_ms": 15,
        "tool": {"call_id": "call_123", "name": "search_documents"},
        "success": True,
    }
    assert "secret argument" not in serialized
    assert "secret result" not in serialized
    assert "private data" not in serialized


def test_web_search_progress_exposes_cost_and_usage_without_query_or_sources():
    payload = agent_status_payload(
        AgentLoopEvent(
            kind="tool_completed",
            step_number=1,
            tool_call=ToolCallRequest(
                id="c1", name="search_web", arguments={"query": "PRIVATE"}, raw_arguments="{}"
            ),
            usage=TokenUsage(total_tokens=120),
            tool_result=ToolResult.success(
                "PRIVATE answer",
                data={
                    "cost_usd": 0.0101,
                    "search_requests": 1,
                    "sources": ["PRIVATE"],
                    "query": "PRIVATE",
                },
            ),
        )
    )
    assert payload["total_tokens"] == 120
    assert payload["web_search"] == {"cost_usd": 0.0101, "search_requests": 1}
    assert "PRIVATE" not in json.dumps(payload)


def test_agent_status_is_encoded_as_named_sse_event() -> None:
    payload = agent_status_payload(AgentLoopEvent(kind="model_started", step_number=1))

    encoded = sse_event("agent_status", payload)

    assert encoded.startswith("event: agent_status\n")
    assert '"status": "model_started"' in encoded
    assert '"step": 1' in encoded


def test_graph_stream_items_map_to_existing_sse_event_names() -> None:
    status_event = agent_stream_sse(AgentLoopEvent(kind="model_started", step_number=1))
    delta_event = agent_stream_sse(LLMChunk(content="Xin chào"))
    usage_only = agent_stream_sse(LLMChunk(usage=TokenUsage(total_tokens=5)))

    assert status_event is not None
    assert status_event.startswith("event: agent_status\n")
    assert delta_event == 'event: delta\ndata: {"content": "Xin chào"}\n\n'
    assert usage_only is None


def test_approval_status_contains_only_requested_tool_arguments() -> None:
    payload = agent_status_payload(
        AgentLoopEvent(
            kind="tool_approval_required",
            step_number=2,
            tool_call=ToolCallRequest(
                id="call_approval",
                name="sensitive_tool",
                arguments={"action": "send demo"},
                raw_arguments='{"action":"send demo"}',
            ),
            approval_id="approval-123",
        )
    )

    assert payload["approval"] == {
        "id": "approval-123",
        "arguments": {"action": "send demo"},
        "approved": None,
    }


def test_model_status_includes_safe_token_count() -> None:
    payload = agent_status_payload(
        AgentLoopEvent(
            kind="model_completed",
            step_number=1,
            usage=TokenUsage(total_tokens=42),
            finish_reason="stop",
        )
    )

    assert payload["total_tokens"] == 42
    assert payload["cached_input_tokens"] == 0
    assert payload["cache_miss_input_tokens"] == 0
    assert payload["prompt_cache_hit_rate"] is None


def test_model_status_includes_context_breakdown() -> None:
    payload = agent_status_payload(
        AgentLoopEvent(
            kind="model_started",
            step_number=1,
            context_usage=ContextTokenUsage(
                estimator="utf8_bytes_v1",
                system_tokens=20,
                memory_tokens=0,
                history_tokens=30,
                tool_tokens=40,
                rag_tokens=0,
                total_tokens=90,
                max_input_tokens=32_000,
                trimmed_messages=2,
            ),
        )
    )

    assert payload["context"] == {
        "estimator": "utf8_bytes_v1",
        "system_tokens": 20,
        "memory_tokens": 0,
        "history_tokens": 30,
        "summary_tokens": 0,
        "tool_tokens": 40,
        "rag_tokens": 0,
        "total_tokens": 90,
        "budget_tokens": 32_000,
        "trimmed_messages": 2,
        "prompt_version": "personal-agent-system-v4",
        "prefix_fingerprint": "",
        "available_tool_count": 0,
        "loaded_tool_count": 0,
        "tool_selection_mode": "all",
        "status_tokens": 0,
        "skill_tokens": 0,
        "selected_skill": None,
    }


def test_model_started_status_contains_goal_progress_remaining_and_budget() -> None:
    status = AgentStatusBlock(
        goal="Tính 12 nhân 4",
        progress=("Đã nhận yêu cầu.",),
        remaining_work=("Gọi tool phù hợp.",),
        budget=AgentStatusBudget(
            current_step=1,
            max_steps=8,
            used_tokens=0,
            max_tokens=20_000,
            elapsed_seconds=0,
            max_seconds=120,
        ),
    )

    payload = agent_status_payload(
        AgentLoopEvent(kind="model_started", step_number=1, status_block=status)
    )

    assert payload["agent_status"] == status.to_payload()
