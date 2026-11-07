import subprocess
import sys

from google_fixtures import google_db  # noqa: F401


def test_offline_gate_rejects_missing_or_non_test_database(monkeypatch):
    from scripts import check_automation_workflow as gate

    monkeypatch.delenv("GOOGLE_TEST_DATABASE_URL", raising=False)
    assert gate.main(["--offline"]) == 2
    monkeypatch.setenv("GOOGLE_TEST_DATABASE_URL", "postgresql://localhost/production")
    assert gate.main(["--offline"]) == 2


def test_live_gate_requires_explicit_selected_automation():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_automation_workflow", "--live"],
        capture_output=True,
    )
    assert result.returncode == 2 and b"--automation-id" in result.stderr


async def test_live_command_enqueues_only_selected_draft_without_enabling_schedule(
    google_db, monkeypatch  # noqa: F811
):  # noqa: F811
    from automation_fixtures import runtime
    from sqlalchemy import select
    from test_automation_api import draft
    from test_automation_execution import Model

    from backend.models.automation import Automation, AutomationRun
    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.service import create_automation
    from scripts.check_automation_workflow import live

    runtime(google_db, monkeypatch, Model())
    async with google_db.sessions() as session:
        view = await create_automation(
            session, google_db.owner, AutomationDraft.model_validate(draft())
        )
    assert await live(view.id, 0) == 2  # Monitor timeout never cancels or starts a second run.
    async with google_db.sessions() as session:
        runs = (await session.scalars(select(AutomationRun))).all()
        assert len(runs) == 1 and runs[0].automation_id == view.id and runs[0].status == "queued"
        assert not (await session.get(Automation, view.id)).enabled
