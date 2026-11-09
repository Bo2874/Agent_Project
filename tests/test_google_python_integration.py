"""Real PostgreSQL journal and host HTTP bridge; no real Google account."""
# ruff: noqa: F811

import json
from dataclasses import asdict, replace
from io import BytesIO
from uuid import uuid4

import pytest
from google_fixtures import google_db  # noqa: F401
from sqlalchemy import select
from test_google_file_access import selected

from artifact_runtime.protocol import PythonCodeInput
from backend.models import AgentOperation, AgentRun, GoogleMutation
from backend.services.artifacts.storage import ArtifactStorage
from backend.services.google.bridge import GooglePythonBridge
from backend.services.google.context import GoogleContextService
from backend.services.google.errors import GoogleServiceError


async def native_bridge(db, tmp_path, file_id="sheet-a"):
    access, context, ref = await selected(db, file_id)
    async with db.sessions() as session:
        run = AgentRun(id=uuid4(), conversation_id=context.conversation_id, status="running")
        session.add(run)
        await session.commit()
    scope = GoogleContextService(access=access, sessions=db.sessions)
    context = await scope.pin(replace(context, run_id=run.id, operation_key="python-call-1"))
    bridge = GooglePythonBridge(
        access, db.sessions, ArtifactStorage(tmp_path), context_service=scope
    )
    return bridge, context, ref


async def native_plan(bridge, context, ref, body=None):
    body = body or {
        "requests": [
            {
                "appendCells": {
                    "sheetId": 0,
                    "rows": [
                        {
                            "values": [
                                {"userEnteredValue": {"stringValue": "https://new.example/repo"}},
                                {"userEnteredValue": {"stringValue": "Fixture"}},
                            ]
                        }
                    ],
                    "fields": "userEnteredValue",
                }
            }
        ]
    }
    args = PythonCodeInput(
        code="print('prepared')",
        google_inputs=[{"ref": ref.model_dump(mode="json")}],
        outputs=[{"path": "changes.json", "publish": False}],
        google_actions=[{"kind": "batch_update", "input_index": 0, "request_path": "changes.json"}],
    )
    snapshot = await bridge.capture(context, args.google_inputs[0])
    control = bridge.storage.put(
        BytesIO(json.dumps(body).encode()),
        f"{context.user_id}/{uuid4()}/{uuid4()}/content.json",
        max_bytes=2097152,
    )
    return args, [snapshot], [asdict(control)]


async def write_fixture(scope, context, ref):
    """Reusable current-path write for Automation stop/notification tests."""
    bridge = GooglePythonBridge(
        scope.access, scope.sessions, scope.test_storage, context_service=scope
    )
    args, snapshots, files = await native_plan(bridge, context, ref)
    return await bridge.execute(context, args, snapshots, files, [], "fixture-write")


async def test_native_sheet_write_is_automatic_and_replays_once(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args, snapshots, files = await native_plan(bridge, context, ref)
    first = await bridge.execute(context, args, snapshots, files, [], "python-receipt")
    second = await bridge.execute(context, args, snapshots, files, [], "python-receipt")
    assert first == second and len(db.provider.writes) == 1
    assert db.provider.sheet_values[-1] == ["https://new.example/repo", "Fixture"]
    assert "canary" not in json.dumps(first)
    async with db.sessions() as session:
        rows = (await session.scalars(select(GoogleMutation))).all()
        assert len(rows) == 1 and rows[0].status == "completed"


async def test_lost_response_blocks_original_and_new_keys(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args, snapshots, files = await native_plan(bridge, context, ref)
    db.provider.lose_write_response = True
    for key in ["python-receipt", "python-receipt", "new-key"]:
        with pytest.raises(GoogleServiceError) as error:
            await bridge.execute(context, args, snapshots, files, [], key)
        assert error.value.code == "google_outcome_unknown"
    assert len(db.provider.writes) == 1


async def test_detected_change_blocks_entire_run_even_new_snapshot(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args, snapshots, files = await native_plan(bridge, context, ref)
    db.provider.files["sheet-a"]["version"] = "2"
    with pytest.raises(GoogleServiceError) as error:
        await bridge.execute(context, args, snapshots, files, [], "python-receipt")
    assert error.value.code == "google_conflict"
    with pytest.raises(GoogleServiceError):
        await bridge.capture(replace(context, operation_key="new-key"), args.google_inputs[0])
    assert not db.provider.writes
    async with db.sessions() as session:
        assert await session.scalar(
            select(AgentOperation.id).where(
                AgentOperation.agent_run_id == context.run_id,
                AgentOperation.key == "google.conflict",
            )
        )


async def test_native_doc_revision_and_target_identity(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path, "doc-a")
    args, snapshots, files = await native_plan(
        bridge,
        context,
        ref,
        {"requests": [{"insertText": {"location": {"index": 1}, "text": "Updated report\n"}}]},
    )
    result = await bridge.execute(context, args, snapshots, files, [], "doc-receipt")
    assert db.provider.doc_text.startswith("Updated report\n")
    assert db.provider.writes[0]["body"]["writeControl"] == {"requiredRevisionId": "1"}
    assert result[0]["url"].endswith("/doc-a/edit")


async def test_revocation_and_foreign_owner_cannot_read_snapshots(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args, _, _ = await native_plan(bridge, context, ref)
    with pytest.raises((GoogleServiceError, RuntimeError)):
        await bridge.capture(replace(context, user_id=db.other), args.google_inputs[0])
    async with db.sessions() as session:
        await bridge.access.revoke_file(session, db.owner, ref.connection_id, ref.file_id)
        await bridge.access.set_files(session, db.owner, ref.connection_id, [ref.file_id])
        await bridge.access.set_work_files(session, db.owner, db.conversation, [ref])
    with pytest.raises((GoogleServiceError, RuntimeError)):
        await bridge.capture(context, args.google_inputs[0])
    assert not db.provider.writes


async def test_docs_revision_race_is_confirmed_conflict_not_unknown(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path, "doc-a")
    args, snapshots, files = await native_plan(
        bridge,
        context,
        ref,
        {"requests": [{"insertText": {"location": {"index": 1}, "text": "New"}}]},
    )
    original = bridge.access.credentials.http.request

    async def revision_race(method, url, **kwargs):
        if method == "POST":
            db.provider.doc_revision += 1
        return await original(method, url, **kwargs)

    bridge.access.credentials.http.request = revision_race
    with pytest.raises(GoogleServiceError) as error:
        await bridge.execute(context, args, snapshots, files, [], "revision-race")
    assert error.value.code == "google_conflict"
    assert not db.provider.writes
    async with db.sessions() as session:
        row = await session.scalar(
            select(GoogleMutation).where(GoogleMutation.run_id == context.run_id)
        )
        assert row.status == "rejected"


async def test_malformed_native_success_is_unknown_and_not_retried(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args, snapshots, files = await native_plan(bridge, context, ref)
    original = bridge.access.credentials.http.request

    async def malformed(method, url, **kwargs):
        result = await original(method, url, **kwargs)
        return {} if method == "POST" else result

    bridge.access.credentials.http.request = malformed
    with pytest.raises(GoogleServiceError) as error:
        await bridge.execute(context, args, snapshots, files, [], "malformed")
    assert error.value.code == "google_outcome_unknown"
    with pytest.raises(GoogleServiceError):
        await bridge.execute(context, args, snapshots, files, [], "malformed")
    assert len(db.provider.writes) == 1


@pytest.mark.parametrize(
    "extension,mode",
    [("xlsx", "keep_format"), ("xlsx", "google_native"), ("docx", "google_native")],
)
async def test_python_generated_file_uploads_and_grants_created_file(  # noqa: F811
    google_db,
    tmp_path,
    extension,
    mode,  # noqa: F811
):  # noqa: F811
    import os

    import httpx

    from artifact_runtime.app import create_app
    from artifact_runtime.containers import DockerBroker
    from backend.services.artifacts.python import PythonToolService
    from backend.services.artifacts.runner_client import ArtifactRunnerClient
    from backend.services.artifacts.service import ArtifactService

    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires Docker runtime")
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path / "private")
    artifacts = ArtifactService(bridge.storage)
    broker = create_app(
        DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker")),
        key="k" * 32,
    )
    service = PythonToolService(
        service=artifacts,
        sessions=db.sessions,
        publication_enabled=True,
        google_bridge=bridge,
        runner_factory=lambda owner: ArtifactRunnerClient(
            "http://runner",
            key="k" * 32,
            storage=bridge.storage,
            owner=owner,
            transport=httpx.ASGITransport(app=broker),
        ),
    )
    code = (
        "from openpyxl import Workbook; w=Workbook(); "
        "w.active['A1']='Report'; w.save('report.xlsx')"
        if extension == "xlsx"
        else "from docx import Document; d=Document(); "
        "d.add_paragraph('Report'); d.save('report.docx')"
    )
    request = PythonCodeInput(
        code=code,
        outputs=[{"path": "report." + extension}],
        google_actions=[
            {
                "kind": "upload",
                "connection_id": ref.connection_id,
                "output_path": "report." + extension,
                "mode": mode,
            }
        ],
    )
    result = await service.execute(context, request)
    assert not result.is_error, result
    receipt = result.data["google_results"][0]
    assert receipt["file_ref"]["file_id"] in db.provider.files
    assert len(result.data["artifact_refs"]) == 1
    assert len(db.provider.writes) == 1
    assert (await service.execute(context, request)) == result
    assert len(db.provider.writes) == 1
    await bridge.scope().validate_authority(context)
    async with db.sessions() as session:
        await bridge.scope().require_ref(
            session, context, ref.__class__.model_validate(receipt["file_ref"])
        )
    if mode == "google_native":
        assert db.provider.files[receipt["file_ref"]["file_id"]]["mimeType"].startswith(
            "application/vnd.google-apps."
        )
