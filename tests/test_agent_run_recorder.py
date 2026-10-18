from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_core.context_budget import ContextTokenUsage
from agent_core.graph import AgentLoopEvent, ToolCallRequest
from agent_core.llm.types import TokenUsage
from backend.models import AgentRun, ModelCall, RunStep, ToolCall
from backend.services import agent_runs
from backend.services.agent_runs import AgentRunRecorder


class FakeSession:
    def __init__(self) -> None:
        self.operations: list[str] = []
        self.records: dict[type, object] = {}
        self.agent_run = SimpleNamespace(
            current_step=0,
            status="running",
            context_trimmed_messages=0,
        )

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, model: type, _record_id: object) -> object:
        self.operations.append(f"get:{model.__name__}")
        if model is AgentRun:
            return self.agent_run
        return self.records.get(model)

    async def scalar(self, _statement: object) -> None:
        return None

    def add(self, instance: object) -> None:
        self.operations.append(f"add:{type(instance).__name__}")
        self.records[type(instance)] = instance

    async def flush(self) -> None:
        self.operations.append("flush")

    async def commit(self) -> None:
        self.operations.append("commit")


async def test_recorder_flushes_model_call_before_inserting_run_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    monkeypatch.setattr(agent_runs, "SessionFactory", lambda: session)
    recorder = AgentRunRecorder(
        agent_run_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        provider="fake",
        model_name="fake-model",
    )

    await recorder(
        AgentLoopEvent(
            kind="model_started",
            step_number=1,
            context_usage=ContextTokenUsage(
                estimator="utf8_bytes_v1",
                system_tokens=10,
                memory_tokens=0,
                history_tokens=20,
                tool_tokens=30,
                rag_tokens=0,
                total_tokens=60,
                max_input_tokens=32_000,
                trimmed_messages=2,
                prompt_version="prompt-v1",
                prefix_fingerprint="a" * 64,
                available_tool_count=10,
                loaded_tool_count=2,
                tool_selection_mode="relevant",
                status_tokens=25,
                summary_tokens=35,
            ),
        )
    )

    assert session.operations == [
        "get:AgentRun",
        "add:ModelCall",
        "flush",
        "add:RunStep",
        "commit",
    ]
    assert session.agent_run.context_total_tokens == 60
    assert session.agent_run.context_trimmed_messages == 2
    assert session.agent_run.available_tool_count == 10
    assert session.agent_run.loaded_tool_count == 2
    assert session.agent_run.tool_selection_mode == "relevant"
    assert session.agent_run.context_status_tokens == 25
    assert session.agent_run.context_summary_tokens == 35
    model_call = session.records[ModelCall]
    assert isinstance(model_call, ModelCall)
    assert model_call.prompt_version == "prompt-v1"
    assert model_call.prompt_fingerprint == "a" * 64


async def test_recorder_persists_cache_hit_and_miss_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    monkeypatch.setattr(agent_runs, "SessionFactory", lambda: session)
    recorder = AgentRunRecorder(
        agent_run_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        provider="fake",
        model_name="fake-model",
    )
    await recorder(AgentLoopEvent(kind="model_started", step_number=1))

    await recorder(
        AgentLoopEvent(
            kind="model_completed",
            step_number=1,
            usage=TokenUsage(
                prompt_tokens=10,
                total_tokens=12,
                cached_input_tokens=4,
                cache_miss_input_tokens=6,
            ),
            finish_reason="stop",
        )
    )

    model_call = session.records[ModelCall]
    run_step = session.records[RunStep]
    assert isinstance(model_call, ModelCall)
    assert isinstance(run_step, RunStep)
    assert model_call.cached_input_tokens == 4
    assert model_call.cache_miss_input_tokens == 6


async def test_recorder_keeps_tool_approval_audit_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    monkeypatch.setattr(agent_runs, "SessionFactory", lambda: session)
    recorder = AgentRunRecorder(
        agent_run_id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        provider="fake",
        model_name="fake-model",
    )
    request = ToolCallRequest(
        id="call_approval",
        name="sensitive_tool",
        arguments={"value": 1},
        raw_arguments='{"value":1}',
    )
    approval_id = uuid4()

    await recorder(AgentLoopEvent(kind="model_started", step_number=1))
    await recorder(AgentLoopEvent(kind="tool_started", step_number=1, tool_call=request))
    await recorder(
        AgentLoopEvent(
            kind="tool_approval_required",
            step_number=1,
            tool_call=request,
            approval_id=str(approval_id),
        )
    )

    tool_call = session.records[ToolCall]
    assert isinstance(tool_call, ToolCall)
    assert tool_call.status == "waiting_approval"
    assert tool_call.approval_id == approval_id
    assert tool_call.approval_requested_at is not None
    assert session.agent_run.status == "waiting_approval"

    await recorder(
        AgentLoopEvent(
            kind="tool_approval_resolved",
            step_number=1,
            tool_call=request,
            approval_id=str(approval_id),
            approved=True,
        )
    )

    assert tool_call.status == "running"
    assert tool_call.approval_decision == "approved"
    assert tool_call.approval_decided_at is not None
    assert session.agent_run.status == "running"
