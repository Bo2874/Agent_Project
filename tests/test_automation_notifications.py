from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from fastapi import FastAPI
from google_fixtures import google_db  # noqa: F401
from sqlalchemy import func, select
from test_automation_controls import active


def application(db, monkeypatch, *, actor=None):
    from types import SimpleNamespace

    from backend.dependencies import get_db_session, get_optional_user
    from backend.models import User
    from backend.routes import automations, notifications, pages

    actor = actor or db.owner
    settings = SimpleNamespace(
        automations_enabled=True, workspace_artifacts_enabled=False, google_connector_enabled=False
    )
    monkeypatch.setattr(automations, "get_settings", lambda: settings)
    monkeypatch.setattr(pages, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(automations.router)
    app.include_router(notifications.router)
    app.include_router(pages.router)

    async def sessions():
        async with db.sessions() as session:
            yield session

    async def user():
        async with db.sessions() as session:
            return await session.get(User, actor)

    app.dependency_overrides[get_db_session] = sessions
    app.dependency_overrides[get_optional_user] = user
    return app


async def test_duplicate_result_event_and_read_flag_are_owner_scoped(google_db, monkeypatch):  # noqa: F811
    from backend.models.automation import AutomationRun
    from backend.models.notification import UserNotification
    from backend.services.automations.notifications import notify_automation_result

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        row.status, row.finished_at = "succeeded", datetime.now(UTC)
        await session.commit()
        await notify_automation_result(session, identity)
        await notify_automation_result(session, identity)
        assert await session.scalar(select(func.count()).select_from(UserNotification)) == 1
        notice = await session.scalar(select(UserNotification))
        assert notice.result["automation_run_id"] == str(identity)
        notice_id = notice.id
    app = application(db, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/notifications")
        assert response.status_code == 200
        assert response.json()["unread_count"] == 1
        assert (await client.post(f"/api/notifications/{notice_id}/read")).status_code == 200
        assert (await client.get("/api/notifications")).json()["unread_count"] == 0
    other = application(db, monkeypatch, actor=db.other)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=other), base_url="http://test"
    ) as client:
        assert (await client.get("/api/notifications")).json()["items"] == []
        assert (await client.post(f"/api/notifications/{notice_id}/read")).status_code == 404
        assert (await client.get(f"/api/automations/runs/{identity}")).status_code == 404


async def test_reload_restores_approval_revision_and_args(google_db, monkeypatch):  # noqa: F811
    from backend.models import AgentApproval, AgentRun, RunStep, ToolCall
    from backend.models.automation import AutomationRun

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    approval_id = uuid4()
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        agent = await session.get(AgentRun, row.agent_run_id)
        agent.status, row.status = "waiting_approval", "waiting_approval"
        step = RunStep(agent_run_id=agent.id, step_number=1, status="running")
        session.add(step)
        await session.flush()
        session.add(
            ToolCall(
                run_step_id=step.id,
                tool_name="run_python_code",
                arguments={"code": "print(1)"},
                status="waiting_approval",
                approval_id=approval_id,
            )
        )
        session.add(
            AgentApproval(
                id=approval_id,
                agent_run_id=agent.id,
                fingerprint="fixture",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await session.commit()
    app = application(db, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(2):
            response = await client.get(f"/api/automations/runs/{identity}")
            assert response.status_code == 200, response.text
            data = response.json()
            assert (
                data["revision_number"] == 1 and data["instruction"] == "Summarize selected inputs"
            )
            assert data["pending_approvals"][0]["id"] == str(approval_id)
            assert data["pending_approvals"][0]["arguments"] == {"code": "print(1)"}


async def test_artifact_results_never_include_another_owners_version(google_db, monkeypatch):  # noqa: F811
    from backend.models import Artifact, ArtifactVersion
    from backend.models.automation import AutomationRun
    from backend.services.automations.execution import sync_automation_run

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        for owner, name in ((db.owner, "owned.xlsx"), (db.other, "private.xlsx")):
            artifact = Artifact(owner_user_id=owner, name=name)
            session.add(artifact)
            await session.flush()
            session.add(
                ArtifactVersion(
                    artifact_id=artifact.id,
                    owner_user_id=owner,
                    version_number=1,
                    name=name,
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    origin="agent",
                    storage_key=uuid4().hex + ".xlsx",
                    size_bytes=1,
                    sha256="f" * 64,
                    operation_key=uuid4().hex,
                    source_run_id=row.agent_run_id,
                )
            )
        await session.commit()
        await sync_automation_run(session, row.id)
        assert [v["name"] for v in row.result["artifact_results"]] == ["owned.xlsx"]
        from backend.models.automation import Automation, AutomationRevision
        from backend.services.automations.service import view

        versions = (await session.scalars(select(ArtifactVersion))).all()
        revision = await session.get(AutomationRevision, row.revision_id)
        revision.artifact_version_ids = [str(version.id) for version in versions]
        await session.commit()
        metadata = await view(session, await session.get(Automation, row.automation_id))
        assert [v["name"] for v in metadata.artifact_inputs] == ["owned.xlsx"]


async def test_schedule_preview_converts_selected_zone_and_rejects_dst_gap(google_db, monkeypatch):  # noqa: F811
    db = google_db
    app = application(db, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/automations/schedules/preview",
            json={"kind": "once", "timezone": "Asia/Ho_Chi_Minh", "at_local": "2026-11-10T08:00"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["schedule"]["at"] == "2026-11-10T01:00:00Z"
        bad = await client.post(
            "/api/automations/schedules/preview",
            json={"kind": "once", "timezone": "America/New_York", "at_local": "2027-03-14T02:30"},
        )
        assert bad.status_code == 422


async def test_inputs_and_tab_metadata_are_selected_file_only(google_db, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from test_google_file_access import selected

    from backend.routes import automations

    db = google_db
    access, _, ref = await selected(db)
    async with db.sessions() as session:
        await access.set_files(session, db.owner, ref.connection_id, ["sheet-a", "office-a"])
    app = application(db, monkeypatch)
    monkeypatch.setattr(
        automations,
        "get_settings",
        lambda: SimpleNamespace(
            automations_enabled=True,
            workspace_artifacts_enabled=False,
            google_connector_enabled=True,
        ),
    )
    monkeypatch.setattr(automations, "google_access", lambda: access, raising=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        inputs = await client.get("/api/automations/inputs")
        assert inputs.status_code == 200, inputs.text
        assert [f["file_id"] for f in inputs.json()["google_files"]] == ["office-a", "sheet-a"]
        tabs = await client.get(f"/api/automations/inputs/google/{ref.connection_id}/sheet-a/tabs")
        assert tabs.status_code == 200 and tabs.json()["items"] == [
            {"sheet_id": 0, "title": "Sheet1"}
        ]
        assert (
            await client.get(f"/api/automations/inputs/google/{ref.connection_id}/doc-a/tabs")
        ).status_code == 404
        assert "canary" not in inputs.text + tabs.text


async def test_missed_tick_is_in_inbox_without_duplicate_dispatch(google_db):  # noqa: F811
    from test_automation_dispatch import due

    from backend.models.notification import UserNotification
    from backend.services.automations.dispatch import dispatch_due

    db = google_db
    first = datetime(2026, 11, 4, 1, tzinfo=UTC)
    await due(db, first)
    async with db.sessions() as session:
        await dispatch_due(session, now=first + timedelta(days=2, minutes=2))
        await dispatch_due(session, now=first + timedelta(days=2, minutes=2))
        notices = (
            await session.scalars(
                select(UserNotification).where(UserNotification.owner_id == db.owner)
            )
        ).all()
        assert len(notices) == 1 and notices[0].result["status"] == "missed"


async def test_unknown_target_survives_reload_for_manual_reconciliation(google_db, monkeypatch):  # noqa: F811
    import pytest
    from test_automation_authority import setup
    from test_google_python_integration import write_fixture

    from backend.models.automation import AutomationRun
    from backend.services.automations.execution import sync_automation_run
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    service, context, ref = await setup(db, monkeypatch)
    db.provider.lose_write_response = True
    with pytest.raises(GoogleServiceError):
        await write_fixture(service, context, ref)
    async with db.sessions() as session:
        row = await session.scalar(
            select(AutomationRun).where(AutomationRun.agent_run_id == context.run_id)
        )
        await sync_automation_run(session, row.id, drained=True)
        identity = row.id
    app = application(db, monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        data = (await client.get(f"/api/automations/runs/{identity}")).json()
        assert data["status"] == "needs_reconciliation"
        assert (
            data["result"]["uncertain_actions"][0]["url"]
            == "https://docs.google.com/spreadsheets/d/sheet-a/edit"
        )
