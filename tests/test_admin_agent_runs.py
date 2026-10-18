from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.dependencies import get_db_session, get_optional_user
from backend.main import app
from backend.models import AgentRun, ModelCall, RunStep, ToolCall
from backend.routes import admin as admin_routes
from backend.services.admin_agent_runs import (
    get_admin_agent_run_detail,
    list_admin_agent_runs,
)


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class FakeListSession:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    async def scalars(self, _: object) -> ScalarRows:
        return ScalarRows(self.rows)


class FakeDetailSession:
    def __init__(self, run: AgentRun, responses: list[list[object]]) -> None:
        self.run = run
        self.responses = responses

    async def scalar(self, _: object) -> AgentRun:
        return self.run

    async def scalars(self, _: object) -> ScalarRows:
        return ScalarRows(self.responses.pop(0))


def make_run(now: datetime) -> AgentRun:
    return AgentRun(
        id=uuid4(),
        conversation_id=uuid4(),
        user_message_id=uuid4(),
        status="completed",
        current_step=1,
        stop_reason="final_answer",
        context_total_tokens=500,
        context_budget_tokens=32_000,
        context_trimmed_messages=2,
        tool_selection_mode="relevance",
        available_tool_count=7,
        loaded_tool_count=3,
        resume_count=1,
        created_at=now,
        finished_at=now + timedelta(seconds=2),
    )


async def test_lists_recent_agent_runs_without_user_content() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    run = make_run(now)

    result = await list_admin_agent_runs(  # type: ignore[arg-type]
        FakeListSession([run]),
        window_days=7,
        now=now,
    )

    assert len(result.runs) == 1
    assert result.runs[0].duration_ms == 2000
    assert result.runs[0].stop_reason == "final_answer"


async def test_builds_agent_run_drill_down_without_arguments_or_output() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    run = make_run(now)
    model_call_id = uuid4()
    step = RunStep(
        id=uuid4(),
        agent_run_id=run.id,
        model_call_id=model_call_id,
        step_number=1,
        attempt_number=1,
        status="completed",
        created_at=now,
        finished_at=now + timedelta(seconds=1),
    )
    model_call = ModelCall(
        id=model_call_id,
        conversation_id=run.conversation_id,
        user_message_id=uuid4(),
        provider="deepseek",
        model_name="deepseek-flash",
        status="completed",
        prompt_tokens=1_000,
        completion_tokens=200,
        total_tokens=1_200,
        cached_input_tokens=600,
        cache_miss_input_tokens=400,
        prompt_version="prompt-v1",
        latency_ms=800,
        created_at=now,
    )
    tool_call = ToolCall(
        id=uuid4(),
        run_step_id=step.id,
        tool_name="demo__calculate",
        arguments={"secret": "must-not-leak"},
        result_content="must-not-leak",
        status="completed",
        duration_ms=20,
        created_at=now,
    )
    session = FakeDetailSession(run, [[step], [model_call], [tool_call]])

    detail = await get_admin_agent_run_detail(session, run_id=run.id)  # type: ignore[arg-type]

    assert detail is not None
    assert detail.total_tokens == 1_200
    assert detail.cache_hit_rate == 0.6
    assert detail.estimated_cost_usd > 0
    assert detail.steps[0].model_call is not None
    assert detail.steps[0].tool_calls[0].tool_name == "demo__calculate"
    payload = detail.model_dump_json()
    assert "must-not-leak" not in payload
    assert "arguments" not in payload
    assert "result_content" not in payload


async def admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), role="admin", is_active=True)


async def fake_session_dependency() -> object:
    yield object()


async def test_agent_run_detail_api_returns_404(monkeypatch) -> None:
    async def fake_detail(_: object, *, run_id) -> None:
        return None

    app.dependency_overrides[get_optional_user] = admin_user
    app.dependency_overrides[get_db_session] = fake_session_dependency
    monkeypatch.setattr(admin_routes, "get_admin_agent_run_detail", fake_detail)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/admin/agent-runs/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Không tìm thấy agent run"
