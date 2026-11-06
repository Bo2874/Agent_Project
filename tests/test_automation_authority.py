import json
from dataclasses import replace

import pytest
from automation_fixtures import runtime
from google_fixtures import google_db  # noqa: F401
from test_automation_api import draft
from test_automation_execution import Model
from test_google_file_access import selected


async def setup(db, monkeypatch, *, create_doc=False):
    from agent_core.tools import ToolContext
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import JobExecutionContext
    from backend.models.automation import Automation
    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.dispatch import enqueue_automation_run
    from backend.services.automations.execution import admit_automation_run
    from backend.services.automations.service import create_automation, current_revision
    from backend.services.google.tools import GoogleToolService

    access, _, ref = await selected(db)
    runtime(db, monkeypatch, Model())
    body = draft(
        grants=[
            {
                "connection_id": ref.connection_id,
                "file_id": ref.file_id,
                "action": "append_rows",
                "range_a1": "Sheet1!A1:B100",
                "sheet_id": 0,
            }
        ]
    )
    if create_doc:
        body["grants"] = [{"connection_id": ref.connection_id, "action": "create_doc"}]
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
    service = GoogleToolService(access=access, sessions=db.sessions)
    context = await service.pin(context)
    ref = ref.model_copy(update={"context_id": context.conversation_id})
    return service, context, ref


async def test_only_exact_granted_proposal_is_automatically_approved(google_db, monkeypatch):  # noqa: F811
    from agent_core.graph.runtime import ToolCallRequest
    from backend.schemas.google import SheetChange
    from backend.services.automations.authority import authorize_automation_call

    db = google_db
    service, context, ref = await setup(db, monkeypatch)
    proposal = await service.sheets.prepare(
        context,
        ref,
        SheetChange(kind="append_rows", range_a1="Sheet1!A1:B100", rows=[["url", "topic"]]),
    )
    args = proposal.commit_arguments()
    request = ToolCallRequest("commit", "google_sheets_append", args, json.dumps(args))
    async with db.sessions() as session:
        assert await authorize_automation_call(session, context, request, google_service=service)
        forged = replace(request, arguments={**args, "digest": "forged"})
        assert not await authorize_automation_call(session, context, forged, google_service=service)
        assert not await authorize_automation_call(
            session,
            context,
            replace(request, name="run_python_code", arguments={"code": "print(1)"}),
            google_service=service,
        )


async def test_revocation_blocks_next_model_boundary_and_old_proposal(google_db, monkeypatch):  # noqa: F811
    from agent_core.tools.types import ToolAuthorizationError
    from backend.services.automations.authority import resolve_run_authority

    db = google_db
    service, context, ref = await setup(db, monkeypatch)
    async with db.sessions() as session:
        await service.access.revoke_file(session, db.owner, ref.connection_id, ref.file_id)
        with pytest.raises(ToolAuthorizationError):
            await resolve_run_authority(session, agent_run_id=context.run_id)


async def test_google_prepare_cannot_use_different_range_or_tab_identity(google_db, monkeypatch):  # noqa: F811
    from agent_core.graph.runtime import ToolCallRequest
    from backend.services.automations.authority import authorize_automation_call

    db = google_db
    service, context, ref = await setup(db, monkeypatch)

    async def allowed(a1):
        args = {
            "ref": ref.model_dump(mode="json"),
            "change": {"kind": "append_rows", "range_a1": a1, "rows": [["url", "topic"]]},
        }
        async with db.sessions() as session:
            return await authorize_automation_call(
                session,
                context,
                ToolCallRequest("prepare", "google_sheets_prepare", args, json.dumps(args)),
                google_service=service,
            )

    assert await allowed("Sheet1!A1:B100")
    assert not await allowed("Sheet1!C1:D100")
    assert not await allowed("Other!A1:B100")


async def test_granted_append_uses_original_executor_and_completed_receipt(google_db, monkeypatch):  # noqa: F811
    from agent_core.graph.runtime import ToolCallRequest
    from agent_core.tools import ToolExecutor, ToolRegistry
    from agent_core.tools.google import register_google_tools
    from backend.services.automations.authority import authorize_automation_call, scope_registry

    db = google_db
    service, context, ref = await setup(db, monkeypatch)
    base = ToolRegistry()
    register_google_tools(base, service)
    registry, executor = scope_registry(base), ToolExecutor()
    assert not base.get("google_sheets_read").policy.requires_approval
    assert registry.get("google_sheets_read").policy.requires_approval
    data = {
        "ref": ref.model_dump(mode="json"),
        "change": {
            "kind": "append_rows",
            "range_a1": "Sheet1!A1:B100",
            "rows": [["new-url", "new-topic"]],
        },
    }
    async with db.sessions() as session:
        approved = await authorize_automation_call(
            session,
            context,
            ToolCallRequest("p", "google_sheets_prepare", data, json.dumps(data)),
            google_service=service,
        )
    prepared = await executor.execute(
        registry, context, "google_sheets_prepare", data, approval_granted=approved
    )
    assert not prepared.is_error, prepared
    args = prepared.data["commit_arguments"]
    async with db.sessions() as session:
        approved = await authorize_automation_call(
            session,
            context,
            ToolCallRequest("c", "google_sheets_append", args, json.dumps(args)),
            google_service=service,
        )
    for _ in range(2):
        result = await executor.execute(
            registry, context, "google_sheets_append", args, approval_granted=approved
        )
        assert not result.is_error, result
    assert db.provider.sheet_values[-1] == ["new-url", "new-topic"]
    assert len(db.provider.writes) == 1


async def test_create_document_grant_can_fill_only_this_runs_new_document(google_db, monkeypatch):  # noqa: F811
    import httpx
    from test_google_docs import DocProvider, approved

    from agent_core.graph.runtime import ToolCallRequest
    from backend.services.automations.authority import authorize_automation_call

    db = google_db
    service, context, ref = await setup(db, monkeypatch, create_doc=True)
    provider = DocProvider(db.provider)
    service.access.credentials.http.transport = httpx.MockTransport(provider.handle)
    proposal = await service.docs.prepare_create(
        context, ref.connection_id, title="Scheduled report"
    )
    receipt = await service.docs.create(
        approved(context, proposal, "google_docs_create"), **proposal.commit_arguments()
    )
    args = {
        "ref": receipt.file_ref.model_dump(mode="json"),
        "edit": {
            "revision_id": "revision-1",
            "tab_id": "tab-1",
            "operations": [{"kind": "insert_text", "start_index": 1, "text": "Report"}],
        },
        "kind": "edit",
    }
    request = ToolCallRequest("edit", "google_docs_prepare", args, json.dumps(args))
    async with db.sessions() as session:
        assert await authorize_automation_call(session, context, request, google_service=service)
        forged = {**args, "ref": {**args["ref"], "file_id": "doc-a"}}
        assert not await authorize_automation_call(
            session, context, replace(request, arguments=forged), google_service=service
        )
