from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.dependencies import get_db_session, get_optional_user
from backend.main import app
from backend.models import AuditLog
from backend.routes import admin as admin_routes
from backend.schemas.admin import AdminAuditLogItem, AdminAuditLogsResponse
from backend.services.audit_logs import list_admin_audit_logs, record_audit_event


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class FakeAuditSession:
    def __init__(self, rows: list[object] | None = None) -> None:
        self.rows = rows or []
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, value: AuditLog) -> None:
        value.id = value.id or uuid4()
        value.created_at = value.created_at or datetime.now(UTC)

    async def scalars(self, _: object) -> ScalarRows:
        return ScalarRows(self.rows)


async def test_records_redacted_audit_metadata() -> None:
    session = FakeAuditSession()

    result = await record_audit_event(  # type: ignore[arg-type]
        session,
        actor_user_id=uuid4(),
        action="admin.agent_run.read",
        target_type="agent_run",
        target_id=str(uuid4()),
        metadata={"password": "abc", "note": "user@example.com"},
    )

    assert result.metadata_json["password"] == "[REDACTED]"
    assert result.metadata_json["note"] == "[REDACTED_EMAIL]"
    assert session.commits == 1


async def test_lists_audit_logs_with_redaction_defense_in_depth() -> None:
    row = AuditLog(
        id=uuid4(),
        actor_user_id=uuid4(),
        action="admin.eval.compare",
        target_type="eval_run",
        target_id=str(uuid4()),
        outcome="success",
        metadata_json={"note": "api_key=leaked"},
        created_at=datetime.now(UTC),
    )

    response = await list_admin_audit_logs(  # type: ignore[arg-type]
        FakeAuditSession([row]),
    )

    assert "leaked" not in str(response.logs[0].metadata)


async def admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), role="admin", is_active=True)


async def fake_session_dependency() -> object:
    yield object()


async def test_audit_log_api_returns_admin_data(monkeypatch) -> None:
    now = datetime.now(UTC)
    response_data = AdminAuditLogsResponse(
        logs=[
            AdminAuditLogItem(
                id=uuid4(),
                actor_user_id=uuid4(),
                action="admin.agent_run.read",
                target_type="agent_run",
                target_id=str(uuid4()),
                outcome="success",
                metadata={},
                created_at=now,
            )
        ]
    )

    async def fake_list(_: object, *, limit: int) -> AdminAuditLogsResponse:
        assert limit == 10
        return response_data

    app.dependency_overrides[get_optional_user] = admin_user
    app.dependency_overrides[get_db_session] = fake_session_dependency
    monkeypatch.setattr(admin_routes, "list_admin_audit_logs", fake_list)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/admin/audit-logs?limit=10")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["logs"][0]["action"] == "admin.agent_run.read"
