from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from agent_core.routing.policy import DecisionPolicy
from backend.database import get_db_session
from backend.dependencies import get_optional_user
from backend.main import app
from backend.routes import admin
from backend.services import routing


@pytest.mark.parametrize("role,status", [(None, 401), ("user", 403), ("admin", 200)])
async def test_routing_api_is_admin_only(role, status, monkeypatch):
    calls = []

    async def current_user():
        return SimpleNamespace(id=uuid4(), role=role, is_active=True) if role else None

    async def session():
        yield object()

    async def metrics(*args, **kwargs):
        calls.append(kwargs)
        return {"decisions": 0, "recent": []}

    monkeypatch.setattr(admin, "routing_metrics", metrics)
    original = dict(app.dependency_overrides)
    app.dependency_overrides[get_optional_user] = current_user
    app.dependency_overrides[get_db_session] = session
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/admin/routing")
        assert response.status_code == status
        assert bool(calls) == (status == 200)
        if status == 200:
            assert "circuit" in response.json() and "key" not in response.text.lower()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


@pytest.mark.parametrize(
    "params", ["limit=0", "days=91", "run_id=wrong", "limit=101", "mode=unknown"]
)
async def test_routing_query_parameters_are_bounded(params):
    async def current_user():
        return SimpleNamespace(id=uuid4(), role="admin", is_active=True)

    async def session():
        yield object()

    original = dict(app.dependency_overrides)
    app.dependency_overrides[get_optional_user] = current_user
    app.dependency_overrides[get_db_session] = session
    try:
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            assert (await client.get("/api/admin/routing?" + params)).status_code == 422
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)


async def test_budget_restored_from_persisted_trace_totals(monkeypatch):
    class Session:
        async def scalar(self, query):
            return uuid4()

        async def execute(self, query):
            return SimpleNamespace(one=lambda: (5, 9000))

    @asynccontextmanager
    async def factory():
        yield Session()

    monkeypatch.setattr(routing, "SessionFactory", factory)
    provider = routing.BackendDecisionProvider(object(), policy=DecisionPolicy())
    run_id = uuid4()
    await provider.bind_run(user_id=uuid4(), run_id=run_id)
    assert provider.calls == 5 and provider.reserved_tokens == 9000
    assert provider.run_id == run_id


async def test_cross_owner_bind_clears_previous_context_and_rejects(monkeypatch):
    class Session:
        async def scalar(self, query):
            return None

    @asynccontextmanager
    async def factory():
        yield Session()

    monkeypatch.setattr(routing, "SessionFactory", factory)
    provider = routing.BackendDecisionProvider(object())
    provider.run_id = uuid4()
    with pytest.raises(RuntimeError):
        await provider.bind_run(user_id=uuid4(), run_id=uuid4())
    assert provider.run_id is None


def test_partial_usage_sums_only_measured_tokens_without_inventing_zero():
    assert routing.token_sum([{}, {"input_tokens": 14}], "input_tokens") == 14
    assert routing.token_sum([{}], "input_tokens") is None
    assert routing.token_sum([{"input_tokens": 0}], "input_tokens") == 0
