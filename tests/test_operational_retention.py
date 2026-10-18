from __future__ import annotations

from datetime import UTC, datetime

from backend.background.handlers import build_background_job_registry
from backend.services.operational_retention import (
    RetentionPolicy,
    purge_expired_operational_data,
)


class ExecuteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeSession:
    def __init__(self, rowcounts: list[int]) -> None:
        self.rowcounts = rowcounts
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object) -> ExecuteResult:
        self.statements.append(str(statement))
        return ExecuteResult(self.rowcounts.pop(0))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_retention_clears_payload_errors_and_expired_rows() -> None:
    session = FakeSession([2, 1, 1, 1, 1, 1, 1, 3, 4, 5])
    policy = RetentionPolicy(
        error_days=30,
        tool_payload_days=30,
        agent_run_days=90,
        background_job_days=30,
        audit_log_days=180,
    )

    report = await purge_expired_operational_data(  # type: ignore[arg-type]
        session,
        policy=policy,
        now=datetime(2026, 9, 29, 12, tzinfo=UTC),
    )

    assert report.tool_payloads_cleared == 2
    assert report.error_messages_cleared == 6
    assert report.agent_runs_deleted == 3
    assert report.background_jobs_deleted == 4
    assert report.audit_logs_deleted == 5
    assert session.commits == 1
    assert any("UPDATE tool_calls" in statement for statement in session.statements)
    assert any("DELETE FROM audit_logs" in statement for statement in session.statements)


def test_background_registry_contains_retention_handler() -> None:
    registry = build_background_job_registry()

    assert "maintenance.purge_operational_data" in registry.names()


async def test_retention_dry_run_rolls_back() -> None:
    session = FakeSession([0] * 10)

    await purge_expired_operational_data(  # type: ignore[arg-type]
        session,
        policy=RetentionPolicy(30, 30, 90, 30, 180),
        now=datetime(2026, 9, 29, 12, tzinfo=UTC),
        apply_changes=False,
    )

    assert session.commits == 0
    assert session.rollbacks == 1
