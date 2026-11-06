import copy
import json
from dataclasses import replace

import httpx
import pytest
from google_fixtures import google_db  # noqa: F401
from test_google_file_access import selected

from agent_core.tools.approval import approval_fingerprint
from backend.schemas.google import DOC_MIME
from backend.services.google.errors import GoogleServiceError


class DocProvider:
    def __init__(self, provider):
        self.provider = provider
        self.revision = "revision-1"
        self.text = "A😀BC\n"
        self.batches, self.creates = [], []
        self.lose_response = False
        self.table = {"startIndex": 8, "endIndex": 12, "table": {"rows": 1, "tableRows": []}}

    def document(self, identity="doc-a"):
        end = 1 + len(self.text.encode("utf-16-le")) // 2
        table = copy.deepcopy(self.table)
        table["startIndex"], table["endIndex"] = end + 1, end + 5
        return {
            "documentId": identity,
            "title": "Selected doc",
            "revisionId": self.revision,
            "tabs": [
                {
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 1,
                                    "endIndex": end,
                                    "paragraph": {
                                        "elements": [
                                            {
                                                "startIndex": 1,
                                                "endIndex": end,
                                                "textRun": {"content": self.text},
                                            }
                                        ]
                                    },
                                },
                                table,
                                {
                                    "startIndex": end + 6,
                                    "endIndex": end + 10,
                                    "paragraph": {
                                        "elements": [
                                            {
                                                "startIndex": end + 6,
                                                "endIndex": end + 10,
                                                "textRun": {"content": "End\n"},
                                            }
                                        ]
                                    },
                                },
                            ]
                        }
                    },
                    "tabProperties": {"tabId": "tab-1", "title": "Main"},
                }
            ],
        }

    async def handle(self, request):
        if request.url.host != "docs.googleapis.com":
            return await self.provider.handle(request)
        if request.method == "GET":
            return httpx.Response(200, json=self.document(request.url.path.rsplit("/", 1)[1]))
        body = json.loads(request.content)
        if request.url.path == "/v1/documents":
            identity = "created-doc-" + str(len(self.creates) + 1)
            self.creates.append(body)
            self.provider.files[identity] = {
                "id": identity,
                "name": body["title"],
                "mimeType": DOC_MIME,
                "capabilities": {"canEdit": True},
            }
            if self.lose_response:
                raise httpx.ReadTimeout("Synthetic creation lost response", request=request)
            return httpx.Response(200, json={"documentId": identity, "title": body["title"]})
        assert request.url.path.endswith(":batchUpdate")
        if body["writeControl"]["requiredRevisionId"] != self.revision:
            return httpx.Response(400, json={"error": {"code": 400}})
        self.batches.append(body)
        raw = self.text.encode("utf-16-le")
        for operation in body["requests"]:
            if "deleteContentRange" in operation:
                span = operation["deleteContentRange"]["range"]
                assert span["tabId"] == "tab-1"
                raw = raw[: (span["startIndex"] - 1) * 2] + raw[(span["endIndex"] - 1) * 2 :]
            else:
                insert = operation["insertText"]
                assert insert["location"]["tabId"] == "tab-1"
                index = (insert["location"]["index"] - 1) * 2
                raw = raw[:index] + insert["text"].encode("utf-16-le") + raw[index:]
        self.text = raw.decode("utf-16-le")
        self.revision = "revision-2"
        if self.lose_response:
            raise httpx.ReadTimeout("Synthetic edit lost response", request=request)
        return httpx.Response(200, json={"documentId": "doc-a", "replies": []})


async def docs(db):
    from backend.services.google.docs import GoogleDocs

    access, context, ref = await selected(db, "doc-a")
    provider = DocProvider(db.provider)
    access.credentials.http.transport = httpx.MockTransport(provider.handle)
    return (
        GoogleDocs(access, db.sessions),
        replace(context, operation_key="doc-edit"),
        ref,
        provider,
    )


def approved(context, proposal, name="google_docs_edit"):
    return replace(
        context, approved_call=(name, approval_fingerprint(name, proposal.commit_arguments()))
    )


async def test_doc_read_and_multiple_utf16_edits_preserve_other_components(google_db):  # noqa: F811
    from backend.schemas.google import DocumentEdit

    service, context, ref, provider = await docs(google_db)
    snapshot = await service.read(context, ref)
    assert snapshot.revision_id == "revision-1"
    assert snapshot.blocks[0].text == "A😀BC\n" and snapshot.blocks[0].tab_id == "tab-1"
    assert snapshot.warnings and snapshot.next_offset is None
    proposal = await service.prepare(
        context,
        ref,
        DocumentEdit(
            revision_id=snapshot.revision_id,
            tab_id="tab-1",
            operations=[
                {"kind": "insert_text", "start_index": 1, "text": "Hi "},
                {"kind": "replace_text", "start_index": 4, "end_index": 5, "text": "bee"},
            ],
        ),
    )
    receipt = await service.execute(approved(context, proposal), **proposal.commit_arguments())
    assert receipt.url == "https://docs.google.com/document/d/doc-a/edit"
    assert provider.text == "Hi A😀beeC\n"
    assert provider.batches[0]["requests"][0]["deleteContentRange"]["range"]["startIndex"] == 4
    assert provider.table == {
        "startIndex": 8,
        "endIndex": 12,
        "table": {"rows": 1, "tableRows": []},
    }
    assert (
        await service.execute(approved(context, proposal), **proposal.commit_arguments())
    ) == receipt
    assert len(provider.batches) == 1
    assert (await service.read(context, ref)).blocks[0].text == "Hi A😀beeC\n"


async def test_doc_revision_change_and_unsupported_or_split_surrogate_edits_rejected(google_db):  # noqa: F811
    from backend.schemas.google import DocumentEdit

    service, context, ref, provider = await docs(google_db)
    for start, end in [(3, 4), (8, 11), (6, 14)]:
        with pytest.raises(GoogleServiceError):
            await service.prepare(
                context,
                ref,
                DocumentEdit(
                    revision_id="revision-1",
                    tab_id="tab-1",
                    operations=[
                        {
                            "kind": "replace_text",
                            "start_index": start,
                            "end_index": end,
                            "text": "x",
                        }
                    ],
                ),
            )
    proposal = await service.prepare(
        context,
        ref,
        DocumentEdit(
            revision_id="revision-1",
            tab_id="tab-1",
            operations=[{"kind": "insert_text", "start_index": 1, "text": "new"}],
        ),
    )
    provider.revision = "changed-outside"
    with pytest.raises(GoogleServiceError, match="snapshot|revision|approval"):
        await service.execute(approved(context, proposal), **proposal.commit_arguments())
    assert provider.batches == []
    with pytest.raises(GoogleServiceError):
        await service.read(replace(context, user_id=google_db.other), ref)
    bad = ref.model_copy(update={"file_id": "sheet-a"})
    with pytest.raises(GoogleServiceError):
        await service.read(context, bad)


async def test_doc_creation_registers_work_file_and_lost_response_never_recreates(google_db):  # noqa: F811
    service, context, ref, provider = await docs(google_db)
    proposal = await service.prepare_create(context, ref.connection_id, title="New document")
    receipt = await service.create(
        approved(context, proposal, "google_docs_create"), **proposal.commit_arguments()
    )
    assert receipt.file_ref.file_id == "created-doc-1"
    async with google_db.sessions() as session:
        files = await service.access.selected(session, context.user_id, context.conversation_id)
        assert "created-doc-1" in {item["file_id"] for item in files}
    assert (
        await service.create(
            approved(context, proposal, "google_docs_create"), **proposal.commit_arguments()
        )
        == receipt
    )
    assert len(provider.creates) == 1
    second = await service.prepare_create(
        replace(context, operation_key="create-2"), ref.connection_id, title="Uncertain document"
    )
    provider.lose_response = True
    for _ in range(2):
        with pytest.raises(GoogleServiceError) as error:
            await service.create(
                approved(context, second, "google_docs_create"), **second.commit_arguments()
            )
        assert error.value.code == "google_outcome_unknown"
    assert len(provider.creates) == 2


async def test_docs_tools_approve_edit_and_use_new_document_in_same_run(google_db):  # noqa: F811
    from test_google_tools import tools

    from agent_core.tools import ToolExecutor

    registry, context, ref, bridge = await tools(google_db, "doc-a")
    provider = DocProvider(google_db.provider)
    bridge.access.credentials.http.transport = httpx.MockTransport(provider.handle)
    executor = ToolExecutor()
    read = await executor.execute(
        registry, context, "google_docs_read", {"ref": ref.model_dump(mode="json")}
    )
    assert not read.is_error, read
    prepared = await executor.execute(
        registry,
        context,
        "google_docs_prepare",
        {
            "kind": "edit",
            "ref": ref.model_dump(mode="json"),
            "edit": {
                "revision_id": "revision-1",
                "tab_id": "tab-1",
                "operations": [{"kind": "insert_text", "start_index": 1, "text": "Hi "}],
            },
        },
    )
    assert not prepared.is_error, prepared
    args = prepared.data["commit_arguments"]
    assert (
        await executor.execute(registry, context, "google_docs_edit", args)
    ).error_code == "tool_approval_required"
    result = await executor.execute(
        registry, context, "google_docs_edit", args, approval_granted=True
    )
    assert not result.is_error, result
    create = await executor.execute(
        registry,
        replace(context, operation_key="new-doc"),
        "google_docs_prepare",
        {"kind": "create", "connection_id": str(ref.connection_id), "title": "From chat"},
    )
    assert not create.is_error, create
    result = await executor.execute(
        registry,
        context,
        "google_docs_create",
        create.data["commit_arguments"],
        approval_granted=True,
    )
    assert not result.is_error, result
    assert result.data["file_ref"]["context_id"] == str(context.conversation_id)
    opened = await executor.execute(
        registry, context, "google_docs_read", {"ref": result.data["file_ref"]}
    )
    assert not opened.is_error, opened


async def test_ready_connection_metadata_available_without_selected_files(google_db):  # noqa: F811
    from test_google_tools import tools

    _, context, ref, bridge = await tools(google_db)
    async with google_db.sessions() as session:
        await bridge.access.set_work_files(session, context.user_id, context.conversation_id, [])
    context = await bridge.pin(context)
    assert context.google_file_refs == ()
    connections = await bridge.connection_context(context)
    assert connections[0]["connection_id"] == str(ref.connection_id)
    assert "canary" not in json.dumps(connections)
