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


async def test_docs_own_write_drive_metadata_catchup_is_not_external_conflict(google_db, tmp_path):  # noqa: F811
    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path, "doc-a")
    body = {"requests": [{"insertText": {"location": {"index": 1}, "text": "Update\n"}}]}
    args, snapshots, files = await native_plan(bridge, context, ref, body)
    await bridge.execute(context, args, snapshots, files, [], "doc-first-write")
    args, snapshots, files = await native_plan(bridge, context, ref, body)
    revision = db.provider.doc_revision
    original = bridge.access.credentials.http.request

    async def delayed_drive_metadata(method, url, **kwargs):
        if method == "GET" and url.startswith("https://docs.googleapis.com/"):
            # The previous write is already reflected in Docs. Drive metadata
            # catches up during preflight without another Docs revision/change.
            db.provider.files["doc-a"]["version"] = "50"
        return await original(method, url, **kwargs)

    bridge.access.credentials.http.request = delayed_drive_metadata
    result = await bridge.execute(context, args, snapshots, files, [], "doc-next-write")
    assert result[0]["status"] == "completed"
    assert len(db.provider.writes) == 2
    assert db.provider.writes[1]["body"]["writeControl"] == {"requiredRevisionId": str(revision)}


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


@pytest.mark.parametrize("status", ["prepared", "executed"])
async def test_snapshot_retained_while_python_receipt_can_resume(google_db, tmp_path, status):
    import os
    import time

    from backend.services.artifacts.maintenance import reconcile_orphans

    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args = PythonCodeInput(
        code="print('read')", google_inputs=[{"ref": ref.model_dump(mode="json")}]
    )
    snapshot = await bridge.capture(context, args.google_inputs[0])
    stored = bridge.storage.path(snapshot["stored"]["key"])
    old = time.time() - 172800
    os.utime(stored, (old, old))
    operation = AgentOperation(
        agent_run_id=context.run_id,
        key="python.receipt.retention",
        fingerprint="a" * 64,
        status=status,
        result={"google_snapshots": [snapshot], "files": []},
    )
    async with db.sessions() as session:
        session.add(operation)
        await session.commit()
    assert await reconcile_orphans(db.sessions, bridge.storage) == 0
    assert stored.exists()
    async with db.sessions() as session:
        row = await session.get(AgentOperation, operation.id)
        row.status = "completed"
        await session.commit()
    assert await reconcile_orphans(db.sessions, bridge.storage) == 1
    assert not stored.exists()


async def test_partial_success_is_reported_when_second_target_conflicts(google_db, tmp_path):
    db = google_db
    bridge, context, sheet = await native_bridge(db, tmp_path)
    doc = sheet.model_copy(update={"file_id": "doc-a"})
    async with db.sessions() as session:
        await bridge.access.set_files(
            session, db.owner, sheet.connection_id, [sheet.file_id, doc.file_id]
        )
        await bridge.access.set_work_files(session, db.owner, db.conversation, [sheet, doc])
    context = await bridge.scope().pin(context)
    _, left, left_files = await native_plan(bridge, context, sheet)
    _, right, right_files = await native_plan(
        bridge,
        context,
        doc,
        {"requests": [{"insertText": {"location": {"index": 1}, "text": "New"}}]},
    )
    args = PythonCodeInput(
        code="print('two targets')",
        google_inputs=[{"ref": sheet.model_dump()}, {"ref": doc.model_dump()}],
        outputs=[{"path": "left.json", "publish": False}, {"path": "right.json", "publish": False}],
        google_actions=[
            {"kind": "batch_update", "input_index": 0, "request_path": "left.json"},
            {"kind": "batch_update", "input_index": 1, "request_path": "right.json"},
        ],
    )
    original = bridge.execute_one

    async def changed(context, plan, kind):
        if plan["snapshot"]["source"]["ref"]["file_id"] == "doc-a":
            db.provider.files["doc-a"]["version"] = "2"
            db.provider.doc_revision += 1
            db.provider.doc_text = "External edit while the first target was being written\n"
        return await original(context, plan, kind)

    bridge.execute_one = changed
    with pytest.raises(GoogleServiceError) as error:
        await bridge.execute(
            context, args, left + right, left_files + right_files, [], "two-targets"
        )
    assert error.value.code == "google_conflict"
    assert len(error.value.completed) == 1
    assert error.value.completed[0]["url"].endswith("/sheet-a/edit")
    assert len(db.provider.writes) == 1
    with pytest.raises(GoogleServiceError) as replay:
        await bridge.execute(
            context, args, left + right, left_files + right_files, [], "two-targets"
        )
    assert replay.value.code == "google_conflict"
    assert replay.value.completed == error.value.completed
    assert len(db.provider.writes) == 1


@pytest.mark.parametrize("commit_reached_database", [False, True])
async def test_remote_write_receipt_commit_loss_never_duplicates(
    google_db, tmp_path, monkeypatch, commit_reached_database
):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession

    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args, snapshots, files = await native_plan(bridge, context, ref)
    original = AsyncSession.commit
    failed = False

    async def commit(session):
        nonlocal failed
        if db.provider.writes and not failed:
            failed = True
            if commit_reached_database:
                await original(session)
            raise OperationalError("receipt commit", {}, RuntimeError("QA lost acknowledgement"))
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    if commit_reached_database:
        first = await bridge.execute(context, args, snapshots, files, [], "receipt-loss")
        assert await bridge.execute(context, args, snapshots, files, [], "receipt-loss") == first
    else:
        with pytest.raises(GoogleServiceError) as error:
            await bridge.execute(context, args, snapshots, files, [], "receipt-loss")
        assert error.value.code == "google_outcome_unknown"
        with pytest.raises(GoogleServiceError):
            await bridge.execute(context, args, snapshots, files, [], "new-receipt-loss-key")
    assert failed and len(db.provider.writes) == 1


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


async def test_confirmed_invalid_batch_is_rejected_and_can_be_corrected(google_db, tmp_path):
    import httpx

    db = google_db
    bridge, context, ref = await native_bridge(db, tmp_path)
    args, snapshots, files = await native_plan(bridge, context, ref)
    original = bridge.access.credentials.http.transport

    async def invalid(request):
        if request.method == "POST":
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
        return await db.provider.handle(request)

    bridge.access.credentials.http.transport = httpx.MockTransport(invalid)
    with pytest.raises(GoogleServiceError) as error:
        await bridge.execute(context, args, snapshots, files, [], "invalid-native")
    assert error.value.code == "google_request_rejected"
    assert not db.provider.writes
    bridge.access.credentials.http.transport = original
    result = await bridge.execute(context, args, snapshots, files, [], "corrected-native")
    assert result[0]["status"] == "completed" and len(db.provider.writes) == 1


@pytest.mark.parametrize(
    "extension,mode,unsafe",
    [
        ("xlsx", "keep_format", False),
        ("xlsx", "google_native", False),
        ("docx", "google_native", False),
        ("xlsx", "google_native", True),
    ],
)
async def test_python_generated_file_uploads_and_grants_created_file(  # noqa: F811
    google_db,
    tmp_path,
    extension,
    mode,  # noqa: F811
    unsafe,
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
    if unsafe:
        code = (
            "from openpyxl import Workbook; w=Workbook(); "
            "w.active['A1']='=IMPORTDATA(\"https://outside.example/data.csv\")'; "
            "w.save('report.xlsx')"
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
    if unsafe:
        assert result.is_error, result
        assert result.error_code == "google_invalid_action"
        assert len(result.data["artifact_refs"]) == 1
        assert result.data["google_results"] == []
        assert db.provider.writes == []
        assert await service.execute(context, request) == result
        return
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
    else:
        from uuid import UUID

        from artifact_runtime.google_types import PythonGoogleInput
        from backend.models.artifact import ArtifactVersion
        from backend.schemas.google import GoogleFileRef

        created = GoogleFileRef.model_validate(receipt["file_ref"])
        downloaded = await bridge.capture(
            context, PythonGoogleInput(ref=created.model_dump(), mode="download")
        )
        replacement = PythonCodeInput(
            code="print('replacement prepared')",
            google_inputs=[{"ref": created.model_dump(mode="json"), "mode": "download"}],
            outputs=[{"path": "replacement.xlsx"}],
            google_actions=[
                {
                    "kind": "upload",
                    "connection_id": created.connection_id,
                    "output_path": "replacement.xlsx",
                    "mode": "keep_format",
                    "input_index": 0,
                }
            ],
        )
        local_version = result.data["artifact_refs"][0]
        async with db.sessions() as session:
            version = await session.get(ArtifactVersion, UUID(local_version["version_id"]))
            metadata = {
                "key": version.storage_key,
                "size_bytes": version.size_bytes,
                "sha256": version.sha256,
                "mime_type": version.mime_type,
            }
        update = await bridge.execute(
            context, replacement, [downloaded], [metadata], [], "replace-binary"
        )
        assert update[0]["url"].endswith("/" + created.file_id + "/view")
        assert len(db.provider.writes) == 2
        assert any(
            method == "PATCH" for method, path in db.provider.calls if path.startswith("/upload/")
        )
