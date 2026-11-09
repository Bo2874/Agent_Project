import pytest
from automation_fixtures import runtime
from google_fixtures import google_db  # noqa: F401
from test_automation_api import draft
from test_automation_execution import Model
from test_google_file_access import selected


async def setup(db, monkeypatch, *, action="edit_sheet"):
    from agent_core.tools import ToolContext
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import JobExecutionContext
    from backend.models.automation import Automation
    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.dispatch import enqueue_automation_run
    from backend.services.automations.execution import admit_automation_run
    from backend.services.automations.service import create_automation, current_revision
    from backend.services.google.context import GoogleContextService

    access, _, ref = await selected(db)
    settings, _ = runtime(db, monkeypatch, Model())
    settings.google_connector_enabled = True
    body = draft(
        grants=[
            {
                "connection_id": ref.connection_id,
                "file_id": ref.file_id,
                "action": action,
                "range_a1": "Sheet1!A1:B100",
                "sheet_id": 0,
            }
        ]
    )
    if action == "edit_sheet":
        body["grants"][0].pop("range_a1")
        body["grants"][0].pop("sheet_id")
    async with db.sessions() as session:
        view = await create_automation(session, db.owner, AutomationDraft.model_validate(body))
        auto = await session.get(Automation, view.id)
        row = await enqueue_automation_run(
            session,
            auto,
            await current_revision(session, auto),
            trigger="manual",
            idempotency_key="one",
        )
        await session.commit()
    queue = PostgresJobQueue()
    claimed = await queue.claim_next(queue_name="default", worker_id="authority", lease_seconds=60)
    async with db.sessions() as session:
        row = await admit_automation_run(session, JobExecutionContext(queue, claimed), row.id)
        context = ToolContext(db.owner, row.conversation_id, "user", run_id=row.agent_run_id)
    service = GoogleContextService(access=access, sessions=db.sessions)
    context = await service.pin(context)
    ref = ref.model_copy(update={"context_id": context.conversation_id})
    from pathlib import Path
    from uuid import uuid4

    from backend.services.artifacts.storage import ArtifactStorage

    service.test_storage = ArtifactStorage(
        Path(".superpowers/verification/google-python/authority") / uuid4().hex
    )
    return service, context, ref


async def test_native_edit_uses_explicit_revision_grant(google_db, monkeypatch):  # noqa: F811
    from test_google_python_integration import write_fixture

    service, context, ref = await setup(google_db, monkeypatch)
    receipt = await write_fixture(service, context, ref)
    assert receipt[0]["status"] == "completed"
    assert len(google_db.provider.writes) == 1


async def test_old_ranged_grant_does_not_become_native_edit(google_db, monkeypatch):  # noqa: F811
    from test_google_python_integration import write_fixture

    from backend.services.google.errors import GoogleServiceError

    service, context, ref = await setup(google_db, monkeypatch, action="append_rows")
    with pytest.raises(GoogleServiceError):
        await write_fixture(service, context, ref)
    assert not google_db.provider.writes


async def test_revoked_grant_blocks_next_boundary(google_db, monkeypatch):  # noqa: F811
    from backend.services.automations.authority import resolve_run_authority

    service, context, ref = await setup(google_db, monkeypatch)
    async with google_db.sessions() as session:
        await service.access.revoke_file(session, google_db.owner, ref.connection_id, ref.file_id)
        with pytest.raises(RuntimeError):
            await resolve_run_authority(session, agent_run_id=context.run_id)


async def test_disabled_connector_blocks_automation(google_db, monkeypatch):  # noqa: F811
    from backend.services.automations import authority

    _, context, _ = await setup(google_db, monkeypatch)
    authority.get_settings().google_connector_enabled = False
    async with google_db.sessions() as session:
        with pytest.raises(RuntimeError):
            await authority.resolve_run_authority(session, agent_run_id=context.run_id)


async def test_read_range_does_not_follow_reused_tab_title(google_db, monkeypatch):  # noqa: F811
    from artifact_runtime.google_types import PythonGoogleInput
    from backend.services.google.bridge import GooglePythonBridge
    from backend.services.google.errors import GoogleServiceError

    scope, context, ref = await setup(google_db, monkeypatch, action="read_sheet")
    bridge = GooglePythonBridge(
        scope.access, scope.sessions, scope.test_storage, context_service=scope
    )
    original = scope.access.credentials.http.request

    async def renamed(method, url, **kwargs):
        result = await original(method, url, **kwargs)
        if "/spreadsheets/" in url:
            result["sheets"][0]["properties"]["sheetId"] = 99
        return result

    scope.access.credentials.http.request = renamed
    with pytest.raises(GoogleServiceError):
        await bridge.capture(
            context, PythonGoogleInput(ref=ref.model_dump(), range_a1="Sheet1!A1:B10")
        )
