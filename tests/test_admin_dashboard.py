from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.dependencies import get_db_session, get_optional_user
from backend.main import app
from backend.routes import admin as admin_routes
from backend.schemas.admin import (
    AdminActivityMetrics,
    AdminCacheMetrics,
    AdminCostMetrics,
    AdminErrorMetrics,
    AdminLatencyMetrics,
    AdminOverviewResponse,
    AdminTokenMetrics,
    AdminToolMetrics,
)
from backend.services.admin_metrics import get_admin_overview


class ExecuteResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def one(self) -> object:
        return self.value

    def all(self) -> object:
        return self.value


class ScalarResult:
    def __init__(self, value: list[object]) -> None:
        self.value = value

    def all(self) -> list[object]:
        return self.value


class FakeSession:
    def __init__(self, now: datetime) -> None:
        model_call = SimpleNamespace(
            model_name="deepseek-chat",
            status="completed",
            total_tokens=120,
            latency_ms=850,
            prompt_version="prompt-v1",
            created_at=now,
        )
        tool_call = SimpleNamespace(
            tool_name="demo__calculate",
            status="completed",
            duration_ms=25,
            error_code=None,
            error_message=None,
            created_at=now,
        )
        failed_run = SimpleNamespace(
            status="failed",
            stop_reason="llm_provider_error",
            error_message="Provider tạm thời lỗi.",
            created_at=now,
        )
        failed_model = SimpleNamespace(
            status="failed",
            error_message="Model timeout password=secret user@example.com",
            created_at=now,
        )
        failed_tool = SimpleNamespace(
            status="failed",
            error_code="tool_timeout",
            error_message="Tool timeout.",
            created_at=now,
        )
        self.execute_values = [
            (5, 400, 100, 500, 80, 320, 750.4),
            (640.2, 1400.8),
            [("completed", 3), ("failed", 1)],
            [("completed", 2), ("failed", 1), ("denied", 1)],
            (40.6, 25.2, 90.7),
        ]
        self.scalar_values = [6, 1, 0]
        self.scalars_values = [
            [model_call],
            [tool_call],
            [failed_run],
            [failed_model],
            [failed_tool],
        ]

    async def execute(self, _: object) -> ExecuteResult:
        return ExecuteResult(self.execute_values.pop(0))

    async def scalar(self, _: object) -> object:
        return self.scalar_values.pop(0)

    async def scalars(self, _: object) -> ScalarResult:
        return ScalarResult(self.scalars_values.pop(0))


def sample_overview() -> AdminOverviewResponse:
    now = datetime.now(UTC)
    return AdminOverviewResponse(
        window_days=7,
        from_time=now,
        to_time=now,
        tokens=AdminTokenMetrics(
            total=100,
            prompt=80,
            completion=20,
            cached_input=10,
            cache_miss_input=70,
        ),
        cache=AdminCacheMetrics(hit_tokens=10, miss_tokens=70, hit_rate=0.125),
        cost=AdminCostMetrics(
            estimated_total=0.0001,
            cached_input=0.00001,
            cache_miss_input=0.00003,
            output=0.00006,
        ),
        activity=AdminActivityMetrics(
            agent_runs=2,
            model_turns=3,
            model_calls=4,
            tool_calls=1,
        ),
        latency=AdminLatencyMetrics(
            average_model_ms=500,
            model_p50_ms=450,
            model_p95_ms=900,
            average_tool_ms=20,
            tool_p50_ms=15,
            tool_p95_ms=40,
        ),
        errors=AdminErrorMetrics(total=0, agent_runs=0, model_calls=0, tool_calls=0),
        tools=AdminToolMetrics(completed=1, failed=0, denied=0, waiting_approval=0),
        run_statuses={"completed": 2},
        recent_model_calls=[],
        recent_tool_calls=[],
        recent_errors=[],
        alerts=[],
    )


async def admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), role="admin", is_active=True)


async def normal_user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), role="user", is_active=True)


async def fake_session_dependency() -> object:
    yield object()


async def test_admin_metrics_aggregate_operational_data() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)

    result = await get_admin_overview(  # type: ignore[arg-type]
        FakeSession(now),
        window_days=7,
        now=now,
    )

    assert result.tokens.total == 500
    assert result.activity.model_calls == 5
    assert result.activity.model_turns == 6
    assert result.activity.agent_runs == 4
    assert result.activity.tool_calls == 4
    assert result.latency.average_model_ms == 750
    assert result.latency.model_p50_ms == 640
    assert result.latency.model_p95_ms == 1401
    assert result.latency.average_tool_ms == 41
    assert result.latency.tool_p50_ms == 25
    assert result.latency.tool_p95_ms == 91
    assert result.cache.hit_rate == 0.2
    assert result.cost.estimated_total > 0
    assert result.errors.total == 3
    assert result.tools.completed == 2
    assert result.tools.denied == 1
    assert [alert.code for alert in result.alerts] == ["model_failure_rate"]
    assert [item.source for item in result.recent_errors] == [
        "agent_run",
        "model_call",
        "tool_call",
    ]
    assert "secret" not in result.recent_errors[1].message
    assert "user@example.com" not in result.recent_errors[1].message


async def test_admin_page_renders_dashboard_for_admin() -> None:
    app.dependency_overrides[get_optional_user] = admin_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Agent operations" in response.text
    assert "data-admin-dashboard" in response.text
    assert "Pass rate theo phiên bản" in response.text
    assert "data-admin-eval-runs" in response.text
    assert "data-admin-agent-runs" in response.text
    assert "data-run-detail-steps" in response.text
    assert "data-admin-alerts" in response.text
    assert "data-admin-audit-logs" in response.text
    assert "/static/admin.js" in response.text


async def test_admin_page_forbids_normal_user() -> None:
    app.dependency_overrides[get_optional_user] = normal_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


async def test_admin_overview_api_requires_authentication() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/admin/overview")

    assert response.status_code == 401


async def test_admin_overview_api_returns_metrics_for_admin(
    monkeypatch,
) -> None:
    async def fake_overview(_: object, *, window_days: int) -> AdminOverviewResponse:
        assert window_days == 30
        return sample_overview()

    app.dependency_overrides[get_optional_user] = admin_user
    app.dependency_overrides[get_db_session] = fake_session_dependency
    monkeypatch.setattr(admin_routes, "get_admin_overview", fake_overview)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/admin/overview?days=30")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tokens"]["total"] == 100
    assert response.json()["activity"]["model_turns"] == 3
    assert response.json()["cache"]["hit_rate"] == 0.125


async def test_admin_overview_api_forbids_normal_user() -> None:
    app.dependency_overrides[get_optional_user] = normal_user
    app.dependency_overrides[get_db_session] = fake_session_dependency
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/admin/overview")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
