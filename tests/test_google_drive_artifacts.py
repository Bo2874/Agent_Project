# ruff: noqa: F811
import io
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from google_fixtures import google_db  # noqa: F401
from test_google_tools import tools

from agent_core.tools import ToolExecutor
from agent_core.tools.approval import approval_fingerprint
from backend.schemas.google import DOC_MIME, SHEET_MIME
from backend.services.artifacts.service import ArtifactService
from backend.services.artifacts.storage import MIME_TYPES, ArtifactStorage
from backend.services.artifacts.tools import ArtifactToolService
from backend.services.artifacts.validation import ValidatedArtifact
from backend.services.google.errors import GoogleServiceError


class UploadProvider:
    def __init__(self, provider):
        self.provider = provider
        self.initializations, self.uploads = [], []
        self.location = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&upload_id=fixture-session"
        self.lose_response = False

    async def handle(self, request):
        if request.url.path != "/upload/drive/v3/files":
            return await self.provider.handle(request)
        if request.method == "POST":
            metadata = json.loads(request.content)
            assert request.headers["x-upload-content-type"]
            self.initializations.append(metadata)
            return httpx.Response(200, headers={"Location": self.location})
        assert request.method == "PUT" and request.url.host == "www.googleapis.com"
        self.uploads.append(request.content)
        identity = "uploaded-" + str(len(self.uploads))
        metadata = self.initializations[-1]
        item = {
            "id": identity,
            "name": metadata["name"],
            "mimeType": metadata["mimeType"],
            "capabilities": {"canEdit": True},
        }
        self.provider.files[identity] = item
        if self.lose_response:
            raise httpx.ReadTimeout("Synthetic upload response lost", request=request)
        return httpx.Response(200, json=item)


async def exported(db, tmp_path, format="xlsx"):
    from backend.services.google.drive import GoogleDrive

    registry, context, ref, bridge = await tools(db)
    storage = ArtifactService(ArtifactStorage(tmp_path))
    stream = io.BytesIO()
    if format == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        workbook.active.append(["Title", "Value"])
        workbook.active.append(["Fixture", 12])
        workbook.save(stream)
    else:
        from docx import Document

        document = Document()
        document.add_paragraph("Vietnamese report fixture")
        document.save(stream)
    content = stream.getvalue()
    stored = storage.storage.put(
        io.BytesIO(content), f"{db.owner}/{uuid4()}/{uuid4()}/content.{format}", max_bytes=20971520
    )
    async with db.sessions() as session:
        version = await storage.publish_version(
            session,
            db.owner,
            artifact_id=None,
            parent_version_id=None,
            staged=ValidatedArtifact(stored=stored, name="report." + format, origin="upload"),
            operation_key=uuid4().hex,
        )
    context = replace(context, artifact_version_ids=(version.version_id,))
    provider = UploadProvider(db.provider)
    bridge.access.credentials.http.transport = httpx.MockTransport(provider.handle)
    artifacts = ArtifactToolService(service=storage, sessions=db.sessions)
    drive = GoogleDrive(bridge.access, db.sessions, artifacts=artifacts)
    bridge.drive = drive
    return drive, context, ref, version, provider, content, registry


def approved(context, proposal):
    name = "google_drive_upload"
    return replace(
        context, approved_call=(name, approval_fingerprint(name, proposal.commit_arguments()))
    )


@pytest.mark.parametrize(
    "format,mode,mime",
    [
        ("xlsx", "keep_format", MIME_TYPES["xlsx"]),
        ("docx", "keep_format", MIME_TYPES["docx"]),
        ("xlsx", "google_native", SHEET_MIME),
        ("docx", "google_native", DOC_MIME),
    ],
)
async def test_owned_version_export_modes_and_completed_replay(
    google_db,
    tmp_path,
    format,
    mode,
    mime,  # noqa: F811
):  # noqa: F811
    drive, context, ref, version, provider, content, _ = await exported(google_db, tmp_path, format)
    proposal = await drive.prepare(context, ref.connection_id, version.version_id, mode=mode)
    assert mode in proposal.preview.after
    if mode == "google_native":
        assert (
            "conversion" in proposal.preview.warning.lower()
            and "loss" in proposal.preview.warning.lower()
        )
    result = await drive.execute(approved(context, proposal), **proposal.commit_arguments())
    assert result.file_ref.file_id == "uploaded-1"
    assert provider.uploads == [content]
    assert provider.initializations == [
        {"name": "report" if mode == "google_native" else "report." + format, "mimeType": mime}
    ]
    assert await drive.execute(approved(context, proposal), **proposal.commit_arguments()) == result
    async with google_db.sessions() as session:
        chosen = await drive.access.selected(session, context.user_id, context.conversation_id)
        assert any(item["file_id"] == "uploaded-1" and item["mime_type"] == mime for item in chosen)
    assert "storage_key" not in json.dumps(result.model_dump(mode="json"))


async def test_upload_foreign_unselected_corrupt_bytes_refused_before_send(google_db, tmp_path):  # noqa: F811
    drive, context, ref, version, provider, _, _ = await exported(google_db, tmp_path)
    for bad_context in (
        replace(context, user_id=google_db.other),
        replace(context, artifact_version_ids=()),
    ):
        with pytest.raises(GoogleServiceError):
            await drive.prepare(
                bad_context, ref.connection_id, version.version_id, mode="keep_format"
            )
    proposal = await drive.prepare(
        context, ref.connection_id, version.version_id, mode="keep_format"
    )
    with pytest.raises(GoogleServiceError):
        await drive.execute(context, **proposal.commit_arguments())
    async with google_db.sessions() as session:
        row = await drive.artifacts.input_version(session, context, version.version_id)
        drive.artifacts.storage_service().storage.path(row.storage_key).write_bytes(b"tampered")
    with pytest.raises(GoogleServiceError):
        await drive.execute(approved(context, proposal), **proposal.commit_arguments())
    assert not provider.initializations and not provider.uploads


async def test_lost_upload_response_unknown_blocks_new_proposal_and_replay(google_db, tmp_path):  # noqa: F811
    drive, context, ref, version, provider, _, _ = await exported(google_db, tmp_path)
    proposal = await drive.prepare(
        context, ref.connection_id, version.version_id, mode="keep_format"
    )
    provider.lose_response = True
    for _ in range(2):
        with pytest.raises(GoogleServiceError) as error:
            await drive.execute(approved(context, proposal), **proposal.commit_arguments())
        assert error.value.code == "google_outcome_unknown"
    with pytest.raises(GoogleServiceError) as error:
        await drive.prepare(
            replace(context, operation_key="new-upload-key"),
            ref.connection_id,
            version.version_id,
            mode="keep_format",
        )
    assert error.value.code == "google_outcome_unknown" and len(provider.uploads) == 1


@pytest.mark.parametrize(
    "location",
    [
        "https://attacker.example/upload",
        "http://www.googleapis.com/upload/drive/v3/files",
        "https://www.googleapis.com/drive/v3/files?upload_id=bad",
    ],
)
async def test_upload_session_location_cannot_redirect_credentials(google_db, tmp_path, location):  # noqa: F811
    drive, context, ref, version, provider, _, _ = await exported(google_db, tmp_path)
    provider.location = location
    proposal = await drive.prepare(
        context, ref.connection_id, version.version_id, mode="keep_format"
    )
    with pytest.raises(GoogleServiceError):
        await drive.execute(approved(context, proposal), **proposal.commit_arguments())
    assert provider.uploads == []


async def test_drive_tools_explicit_mode_and_exact_approval(google_db, tmp_path):  # noqa: F811
    _, context, ref, version, provider, _, registry = await exported(google_db, tmp_path)
    executor = ToolExecutor()
    args = {"connection_id": str(ref.connection_id), "version_id": str(version.version_id)}
    missing_mode = await executor.execute(registry, context, "google_drive_prepare", args)
    assert missing_mode.error_code == "tool_schema_validation_failed"
    prepared = await executor.execute(
        registry, context, "google_drive_prepare", {**args, "mode": "google_native"}
    )
    assert not prepared.is_error, prepared
    commit = prepared.data["commit_arguments"]
    assert (
        await executor.execute(registry, context, "google_drive_upload", commit)
    ).error_code == "tool_approval_required"
    receipt = await executor.execute(
        registry, context, "google_drive_upload", commit, approval_granted=True
    )
    assert not receipt.is_error and len(provider.uploads) == 1, receipt


async def test_office_file_pin_is_revoked_even_without_native_tools(google_db):  # noqa: F811
    from agent_core.tools.types import ToolAuthorizationError

    _, context, ref, bridge = await tools(google_db, "office-a")
    async with google_db.sessions() as session:
        await bridge.access.revoke_file(session, context.user_id, ref.connection_id, ref.file_id)
    with pytest.raises(ToolAuthorizationError):
        await bridge.guard(context, "google_drive_prepare")


def test_resumable_upload_id_is_redacted_from_logs():
    from backend.core.redaction import redact_text

    message = (
        "HTTP Request: PUT https://www.googleapis.com/upload/drive/v3/files"
        '?upload_id=session-secret-canary "200 OK"'
    )
    assert "session-secret-canary" not in redact_text(message)
