"""Real process death/restart, PostgreSQL jobs/checkpoints; no paid/network providers."""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from automation_fixtures import queued, runtime
from google_fixtures import google_db  # noqa: F401
from sqlalchemy import func, select
from test_automation_api import draft
from test_automation_execution import Model
from test_google_file_access import selected


async def child(mode, env, *, expected=0):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("automation_process_fixture.py")),
        mode,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 60)
        Path(env["AUTOMATION_QA_STATE"]).with_name(f"{mode}-{process.pid}.log").write_bytes(output)
        assert process.returncode == expected, output.decode(errors="replace")[-6000:]
        return output.decode(errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


@pytest.mark.parametrize("lost_response", [False, True])
async def test_scheduled_process_recovery_preserves_single_google_write(
    google_db,  # noqa: F811
    monkeypatch,
    tmp_path,
    lost_response,  # noqa: F811
):
    from backend.models import AgentOperation, BackgroundJob, GoogleMutation
    from backend.models.automation import Automation, AutomationRun
    from backend.models.notification import UserNotification as Notification
    from backend.services.automations.service import close_unknown_run, run_now

    db = google_db
    _, _, ref = await selected(db)
    settings, _ = runtime(db, monkeypatch, Model())
    settings.google_connector_enabled = True
    body = draft(
        grants=[
            {
                "connection_id": str(ref.connection_id),
                "file_id": ref.file_id,
                "action": "append_rows",
                "range_a1": "Sheet1!A1:B100",
                "sheet_id": 0,
            }
        ]
    )
    # Create draft only; dispatch must be done by two independent scheduler processes.
    view, unused = await queued(db, body=body)
    async with db.sessions() as session:
        initial = await session.get(AutomationRun, unused)
        job = await session.get(BackgroundJob, initial.job_id)
        initial.status, job.status = "cancelled", "cancelled"
        auto = await session.get(Automation, view.id)
        auto.enabled, auto.next_run_at = True, datetime.now(UTC) + timedelta(milliseconds=100)
        await session.commit()
    path = tmp_path / "provider.json"
    path.write_text(json.dumps({"lose_response": lost_response}), encoding="utf-8")
    env = {
        **os.environ,
        "AUTOMATION_QA_SCHEMA": db.schema,
        "AUTOMATION_QA_STATE": str(path),
        "AUTOMATION_QA_KEY": db.settings.google_credential_encryption_keys.get_secret_value(),
        "GOOGLE_TEST_DATABASE_URL": db.database_url,
    }
    await child("setup", env)
    await asyncio.gather(child("scheduler", env), child("scheduler", env))
    async with db.sessions() as session:
        runs = (
            await session.scalars(select(AutomationRun).where(AutomationRun.trigger == "scheduled"))
        ).all()
        assert len(runs) == 1 and runs[0].status == "queued"
        identity = runs[0].id
    if not lost_response:
        await child("worker", {**env, "AUTOMATION_QA_CRASH": "1"}, expected=91)
        async with db.sessions() as session:
            run = await session.get(AutomationRun, identity)
            mutation = await session.scalar(
                select(GoogleMutation).where(GoogleMutation.run_id == run.agent_run_id)
            )
            assert run.status == "running" and mutation.status == "completed"
        assert len(json.loads(path.read_text())["writes"]) == 1
        await asyncio.sleep(2.1)  # Only this killed test child's lease.
    output = await asyncio.gather(child("worker", env), child("worker", env))
    state = json.loads(path.read_text())
    assert len(state["writes"]) == 1
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        job = await session.get(BackgroundJob, run.job_id)
        assert run.status == ("needs_reconciliation" if lost_response else "succeeded"), (
            run.stop_reason,
            run.result,
            output,
            state,
        )
        assert job.attempt_count == (1 if lost_response else 2)
        assert state["model_calls"] == [
            "search_web",
            "google_sheets_prepare",
            "google_sheets_append",
        ] + ([] if lost_response else ["final"])
        assert run.result["total_tokens"] >= 30_000  # Exceeds configured old cap 128.
        assert (
            await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.event_key.like(f"automation:{run.id}:%")
                )
            )
            == 1
        )
        if lost_response:
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as conflict:
                await run_now(session, db.owner, view.id, idempotency_key="do-not-replay")
            assert conflict.value.status_code == 409
            await close_unknown_run(session, db.owner, run.id)
            mutation = await session.scalar(
                select(GoogleMutation).where(GoogleMutation.run_id == run.agent_run_id)
            )
            assert mutation.status == "unknown"
            assert await session.scalar(
                select(AgentOperation.id).where(
                    AgentOperation.agent_run_id == run.agent_run_id,
                    AgentOperation.status == "started",
                )
            )
    assert len(json.loads(path.read_text())["writes"]) == 1
