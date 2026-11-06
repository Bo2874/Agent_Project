"""Regression cases from the final offline whole-branch review."""

from dataclasses import replace

import httpx
import pytest
from google_fixtures import google_db  # noqa: F401
from test_google_file_access import selected
from test_google_sheets import approved


@pytest.mark.parametrize("body", [b"not-json", b"[]"])
async def test_invalid_google_success_body_is_rejected(body):
    from backend.services.google.client import SHEETS_URL, GoogleHTTP
    from backend.services.google.errors import GoogleServiceError

    client = GoogleHTTP(httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    with pytest.raises(GoogleServiceError):
        await client.request("GET", SHEETS_URL + "/sheet-a/values/A1:B10", token="fixture")


async def test_empty_sheet_requires_a_valid_value_range_envelope(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError
    from backend.services.google.sheets import GoogleSheets

    db = google_db
    access, context, ref = await selected(db)
    original = db.provider.handle
    payload = {}

    async def handle(request):
        if "/values/" in request.url.path:
            return httpx.Response(200, json=payload)
        return await original(request)

    access.credentials.http.transport = httpx.MockTransport(handle)
    service = GoogleSheets(access, db.sessions)
    with pytest.raises(GoogleServiceError):
        await service.read(context, ref, range_a1="Sheet1!A1:B10")
    payload["range"] = "Sheet1!A1:B10"
    assert (await service.read(context, ref, range_a1="Sheet1!A1:B10")).values == []


@pytest.mark.parametrize("kind", ["append_rows", "update_range"])
async def test_invalid_write_envelope_is_unknown_and_never_retried(google_db, kind):  # noqa: F811
    from backend.models import GoogleMutation
    from backend.schemas.google import SheetChange
    from backend.services.google.errors import GoogleServiceError
    from backend.services.google.sheets import GoogleSheets

    db = google_db
    access, context, ref = await selected(db)
    service = GoogleSheets(access, db.sessions)
    proposal = await service.prepare(
        replace(context, operation_key="malformed-write"),
        ref,
        SheetChange(kind=kind, range_a1="Sheet1!A1:B10", rows=[["new", "value"]]),
    )
    original = db.provider.handle

    async def handle(request):
        response = await original(request)
        return httpx.Response(200, json={}) if request.method in {"POST", "PUT"} else response

    access.credentials.http.transport = httpx.MockTransport(handle)
    for _ in range(2):
        with pytest.raises(GoogleServiceError) as error:
            await service.execute(approved(context, proposal), **proposal.commit_arguments())
        assert error.value.code == "google_outcome_unknown"
    assert len(db.provider.writes) == 1
    async with db.sessions() as session:
        assert (await session.get(GoogleMutation, proposal.id)).status == "unknown"


@pytest.mark.parametrize(
    "suggestion",
    [
        "suggestedInsertionIds",
        "suggestedDeletionIds",
        "suggestedTextStyleChanges",
    ],
)
async def test_suggested_doc_runs_cannot_be_edited(google_db, suggestion):  # noqa: F811
    from test_google_docs import docs

    from backend.schemas.google import DocumentEdit
    from backend.services.google.errors import GoogleServiceError

    service, context, ref, provider = await docs(google_db)
    original = provider.document
    requests = []

    def document(identity="doc-a"):
        result = original(identity)
        run = result["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0][
            "textRun"
        ]
        run[suggestion] = {"s-1": {}} if suggestion.endswith("Changes") else ["s-1"]
        return result

    provider.document = document
    original_handle = provider.handle

    async def handle(request):
        if request.url.host == "docs.googleapis.com" and request.method == "GET":
            requests.append(dict(request.url.params))
        return await original_handle(request)

    service.access.credentials.http.transport = httpx.MockTransport(handle)
    read = await service.read(context, ref)
    assert all(block.start_index != 1 for block in read.blocks)
    assert any("suggestion" in warning for warning in read.warnings)
    assert requests[0].get("suggestionsViewMode") == "SUGGESTIONS_INLINE"
    with pytest.raises(GoogleServiceError):
        await service.prepare(
            context,
            ref,
            DocumentEdit(
                revision_id="revision-1",
                tab_id="tab-1",
                operations=[
                    {"kind": "replace_text", "start_index": 1, "end_index": 2, "text": "x"}
                ],
            ),
        )
    assert provider.batches == []


@pytest.mark.parametrize("fmt", [{}, {"bold": None, "italic": None}])
def test_empty_format_is_rejected_before_proposal(fmt):
    from pydantic import ValidationError

    from backend.schemas.google import SheetChange

    with pytest.raises(ValidationError):
        SheetChange(kind="format_range", range_a1="A1:B2", format=fmt)


class FormattingProvider:
    def __init__(self, provider):
        self.provider = provider
        self.tabs = [
            {"sheetId": 10, "title": "Hidden", "hidden": True, "index": 0},
            {"sheetId": 20, "title": "Visible", "index": 1},
        ]
        self.style = {"textFormat": {"bold": False}, "backgroundColor": {"red": 1}}

    async def handle(self, request):
        import copy

        if request.method == "GET" and request.url.path == "/v4/spreadsheets/sheet-a":
            sheets = [{"properties": copy.deepcopy(tab)} for tab in self.tabs]
            if "ranges" in request.url.params:
                title = request.url.params["ranges"].split("!")[0].strip("'")
                sheets = [sheet for sheet in sheets if sheet["properties"]["title"] == title]
                for sheet in sheets:
                    sheet["data"] = [
                        {
                            "startRow": 0,
                            "startColumn": 0,
                            "rowData": [
                                {"values": [{"userEnteredFormat": copy.deepcopy(self.style)}]}
                            ],
                        }
                    ]
            return httpx.Response(200, json={"spreadsheetId": "sheet-a", "sheets": sheets})
        response = await self.provider.handle(request)
        if request.url.path.endswith(":batchUpdate"):
            return httpx.Response(200, json={"spreadsheetId": "sheet-a", "replies": [{}]})
        return response


async def formatting_service(db):
    from backend.services.google.sheets import GoogleSheets

    access, context, ref = await selected(db)
    provider = FormattingProvider(db.provider)
    access.credentials.http.transport = httpx.MockTransport(provider.handle)
    return GoogleSheets(access, db.sessions), context, ref, provider


async def test_tabless_format_pins_first_visible_tab_across_reorder(google_db):  # noqa: F811
    from backend.schemas.google import SheetChange

    service, context, ref, provider = await formatting_service(google_db)
    proposal = await service.prepare(
        context,
        ref,
        SheetChange(
            kind="format_range",
            range_a1="A1:B2",
            format={"bold": True},
        ),
    )
    assert "Visible" in proposal.preview.target
    provider.tabs.reverse()
    provider.tabs[0]["index"], provider.tabs[1]["index"] = 0, 1
    await service.execute(approved(context, proposal), **proposal.commit_arguments())
    request = google_db.provider.writes[-1]["body"]["requests"][0]["repeatCell"]
    assert request["range"]["sheetId"] == 20
    assert len(google_db.provider.writes) == 1


async def test_renamed_tab_requires_new_approval_without_fencing(google_db):  # noqa: F811
    from backend.models import GoogleMutation
    from backend.schemas.google import SheetChange
    from backend.services.google.errors import GoogleServiceError

    service, context, ref, provider = await formatting_service(google_db)
    proposal = await service.prepare(
        context,
        ref,
        SheetChange(
            kind="format_range",
            range_a1="A1:B2",
            format={"bold": True},
        ),
    )
    provider.tabs[1]["title"] = "Renamed"
    with pytest.raises(GoogleServiceError):
        await service.execute(approved(context, proposal), **proposal.commit_arguments())
    assert not google_db.provider.writes
    async with google_db.sessions() as session:
        assert (await session.get(GoogleMutation, proposal.id)).status not in {"sent", "unknown"}


async def test_google_boundary_checks_each_file_once_for_ten_tools(google_db):  # noqa: F811
    from test_google_tools import tools

    from agent_core.graph import stream_agent_graph
    from agent_core.graph.runtime import AgentNodeExecutionError
    from agent_core.llm.types import LLMChunk
    from backend.schemas.google import GoogleFileRef

    db = google_db
    registry, context, ref, service = await tools(db)
    ids = ["sheet-a", *[f"sheet-{index}" for index in range(19)]]
    for identity in ids:
        db.provider.files[identity] = {**db.provider.files["sheet-a"], "id": identity}
    refs = [
        GoogleFileRef(
            connection_id=ref.connection_id, file_id=identity, context_id=context.conversation_id
        )
        for identity in ids
    ]
    async with db.sessions() as session:
        await service.access.set_files(session, context.user_id, ref.connection_id, ids)
        await service.access.set_work_files(session, context.user_id, ref.context_id, refs)
    context = await service.pin(context)
    context = replace(context, authorization_guard=lambda name: service.guard(context, name))
    assert len(registry.list_accessible_tools("user")) == 10
    counts = []

    class Model:
        max_tokens = 100

        async def stream(self, messages, tools=None, **kwargs):
            counts.append(sum(path.startswith("/drive/v3/files/") for _, path in db.provider.calls))
            raise RuntimeError("stop at model boundary")
            yield LLMChunk()  # pragma: no cover

    args = dict(
        model=Model(),
        context=context,
        registry=registry,
        react_enabled=True,
        messages=[{"role": "user", "content": "Read Google Sheets"}],
    )
    for _ in range(2):
        db.provider.calls.clear()
        with pytest.raises(RuntimeError, match="stop at model"):
            _ = [item async for item in stream_agent_graph(**args)]
        # Context preparation and model dispatch are separate fresh boundaries.
        assert counts[-1] == 40
    async with db.sessions() as session:
        await service.access.revoke_file(session, context.user_id, ref.connection_id, ids[-1])
    db.provider.calls.clear()
    with pytest.raises(AgentNodeExecutionError) as error:
        _ = [item async for item in stream_agent_graph(**args)]
    assert error.value.code == "google_authorization_changed"
    assert len(counts) == 2
    assert not db.provider.calls


async def created_doc(db):
    from test_google_docs import DocProvider
    from test_google_docs import approved as approve_doc
    from test_google_tools import tools

    registry, context, ref, bridge = await tools(db, "doc-a")
    provider = DocProvider(db.provider)
    bridge.access.credentials.http.transport = httpx.MockTransport(provider.handle)
    proposal = await bridge.docs.prepare_create(context, ref.connection_id, title="Created scope")
    receipt = await bridge.docs.create(
        approve_doc(context, proposal, "google_docs_create"),
        **proposal.commit_arguments(),
    )
    return registry, context, receipt.file_ref, bridge, provider, proposal


async def test_created_file_revoke_regrant_cannot_replay_creation(google_db):  # noqa: F811
    from agent_core.tools import ToolExecutor

    registry, context, ref, service, _, proposal = await created_doc(google_db)
    async with google_db.sessions() as session:
        await service.access.revoke_file(session, context.user_id, ref.connection_id, ref.file_id)
        from backend.schemas.google import GoogleFileRef

        original = GoogleFileRef(
            connection_id=ref.connection_id, file_id="doc-a", context_id=ref.context_id
        )
        await service.access.set_files(
            session, context.user_id, ref.connection_id, ["doc-a", ref.file_id]
        )
        await service.access.set_work_files(
            session, context.user_id, ref.context_id, [original, ref]
        )
    result = await ToolExecutor().execute(
        registry,
        context,
        "google_docs_create",
        proposal.commit_arguments(),
        approval_granted=True,
    )
    assert result.error_code == "google_authorization_changed"


async def test_created_doc_revoked_during_read_never_discloses_content(google_db):  # noqa: F811
    from agent_core.tools import ToolExecutor

    registry, context, ref, service, provider, _ = await created_doc(google_db)
    original = provider.handle

    async def handle(request):
        response = await original(request)
        if request.url.host == "docs.googleapis.com" and request.method == "GET":
            async with google_db.sessions() as session:
                await service.access.revoke_file(
                    session, context.user_id, ref.connection_id, ref.file_id
                )
        return response

    service.access.credentials.http.transport = httpx.MockTransport(handle)
    result = await ToolExecutor().execute(
        registry,
        context,
        "google_docs_read",
        {"ref": ref.model_dump(mode="json")},
    )
    assert result.error_code == "google_authorization_changed"
    assert "blocks" not in result.content and "snapshot" not in result.data


async def test_revoked_created_doc_blocks_durable_graph_replay(google_db, monkeypatch):  # noqa: F811
    import json

    from langgraph.checkpoint.memory import InMemorySaver

    from agent_core.graph import AgentLoopEvent, stream_agent_graph
    from agent_core.graph.runtime import AgentNodeExecutionError, _fingerprint
    from agent_core.llm.types import LLMChunk, LLMToolCallDelta
    from backend.services import run_events

    registry, context, ref, service, provider, _ = await created_doc(google_db)
    monkeypatch.setattr(run_events, "runtime_session", google_db.sessions)
    journal = run_events.OperationJournal(context.run_id)
    saver = InMemorySaver()
    calls, observations = [], []

    class Model:
        max_tokens = 200

        async def stream(self, messages, tools=None, **kwargs):
            calls.append(messages)
            if not any(message.get("role") == "tool" for message in messages):
                yield LLMChunk(
                    tool_call_deltas=(
                        LLMToolCallDelta(
                            index=0,
                            id="created-read",
                            name="google_docs_read",
                            arguments=json.dumps({"ref": ref.model_dump(mode="json")}),
                        ),
                    ),
                    finish_reason="tool_calls",
                )
            else:
                yield LLMChunk(content="done", finish_reason="stop")

    async def crash_after_journal(event):
        if isinstance(event, AgentLoopEvent) and event.kind == "tool_completed":
            assert not event.tool_result.is_error
            raise RuntimeError("crash after durable observation before checkpoint")

    args = dict(
        model=Model(),
        messages=[{"role": "user", "content": "Read Google Doc"}],
        registry=registry,
        context=context,
        react_enabled=True,
        operation_journal=journal,
        graph_checkpointer=saver,
    )
    with pytest.raises(RuntimeError, match="crash after durable"):
        _ = [item async for item in stream_agent_graph(**args, event_handler=crash_after_journal)]
    assert (
        await journal.load(
            "root.tool.1.created-read",
            _fingerprint(
                {
                    "name": "google_docs_read",
                    "arguments": {"ref": ref.model_dump(mode="json")},
                }
            ),
        )
        is not None
    )
    async with google_db.sessions() as session:
        await service.access.revoke_file(session, context.user_id, ref.connection_id, ref.file_id)
    before = len(calls)
    with pytest.raises(AgentNodeExecutionError) as error:
        async for item in stream_agent_graph(**args, resume=True):
            observations.append(item)
    assert error.value.code == "google_authorization_changed"
    assert len(calls) == before
    assert not any(isinstance(item, AgentLoopEvent) and item.tool_result for item in observations)


@pytest.mark.parametrize(
    "field,value",
    [
        ("textFormat", {"bold": True}),
        ("backgroundColor", {"green": 1}),
        ("numberFormat", {"type": "DATE"}),
    ],
)
async def test_changed_affected_format_requires_new_approval(google_db, field, value):  # noqa: F811
    from backend.schemas.google import SheetChange
    from backend.services.google.errors import GoogleServiceError

    service, context, ref, provider = await formatting_service(google_db)
    proposal = await service.prepare(
        context,
        ref,
        SheetChange(
            kind="format_range",
            range_a1="A1:B2",
            format={"bold": True, "background": "#000000", "number_format": "NUMBER"},
        ),
    )
    assert "bold" in proposal.preview.before and "false" in proposal.preview.before
    provider.style[field] = value
    with pytest.raises(GoogleServiceError, match="snapshot|changed|approval"):
        await service.execute(approved(context, proposal), **proposal.commit_arguments())
    assert not google_db.provider.writes


async def test_doc_edit_missing_response_identity_stays_unknown(google_db):  # noqa: F811
    from test_google_docs import approved as approve_doc
    from test_google_docs import docs

    from backend.models import GoogleMutation
    from backend.schemas.google import DocumentEdit
    from backend.services.google.errors import GoogleServiceError

    service, context, ref, provider = await docs(google_db)
    proposal = await service.prepare(
        context,
        ref,
        DocumentEdit(
            revision_id="revision-1",
            tab_id="tab-1",
            operations=[{"kind": "replace_text", "start_index": 1, "end_index": 2, "text": "x"}],
        ),
    )
    original = provider.handle

    async def handle(request):
        response = await original(request)
        return (
            httpx.Response(200, json={}) if request.url.path.endswith(":batchUpdate") else response
        )

    service.access.credentials.http.transport = httpx.MockTransport(handle)
    for _ in range(2):
        with pytest.raises(GoogleServiceError) as error:
            await service.execute(approve_doc(context, proposal), **proposal.commit_arguments())
        assert error.value.code == "google_outcome_unknown"
    assert len(provider.batches) == 1
    async with google_db.sessions() as session:
        assert (await session.get(GoogleMutation, proposal.id)).status == "unknown"


async def test_revoked_upload_cannot_replay_completed_receipt(google_db, tmp_path):  # noqa: F811
    from test_google_drive_artifacts import approved as approve_upload
    from test_google_drive_artifacts import exported

    from agent_core.tools import ToolExecutor

    drive, context, ref, version, provider, _, registry = await exported(google_db, tmp_path)
    proposal = await drive.prepare(
        context, ref.connection_id, version.version_id, mode="google_native"
    )
    receipt = await drive.execute(approve_upload(context, proposal), **proposal.commit_arguments())
    assert receipt.file_authority.grant_generation == 1
    async with google_db.sessions() as session:
        await drive.access.revoke_file(
            session,
            context.user_id,
            ref.connection_id,
            receipt.file_ref.file_id,
        )
    result = await ToolExecutor().execute(
        registry,
        context,
        "google_drive_upload",
        proposal.commit_arguments(),
        approval_granted=True,
    )
    assert result.error_code == "google_authorization_changed"
    assert len(provider.uploads) == 1
