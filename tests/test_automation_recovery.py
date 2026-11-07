import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from google_fixtures import google_db  # noqa: F401
from test_automation_controls import active


@pytest.mark.parametrize(
    "kind", ["tool_started", "tool_completed", "tool_approval_required", "tool_approval_resolved"]
)
async def test_fresh_recorder_restores_persisted_tool_identity(google_db, monkeypatch, kind):  # noqa: F811
    from agent_core.graph import AgentLoopEvent, ToolCallRequest
    from agent_core.tools import ToolResult
    from backend.models import AgentRun
    from backend.models.automation import AutomationRun
    from backend.services.agent_runs import AgentRunRecorder

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        agent = await session.get(AgentRun, run.agent_run_id)
    kwargs = dict(
        agent_run_id=run.agent_run_id,
        conversation_id=run.conversation_id,
        user_message_id=agent.user_message_id,
        provider="fixture",
        model_name="fixture",
    )
    original = AgentRunRecorder(**kwargs)
    request = ToolCallRequest("resumed", "run_python_code", {"code": "print(1)"}, "{}")
    await original(AgentLoopEvent(kind="model_started", step_number=1))
    await original(AgentLoopEvent(kind="tool_started", step_number=1, tool_call=request))
    fresh = AgentRunRecorder(**kwargs)
    await fresh(
        AgentLoopEvent(
            kind=kind,
            step_number=1,
            tool_call=request,
            tool_result=ToolResult.success("done"),
            approval_id=str(uuid4()),
            approved=True,
        )
    )


async def test_unattended_approval_remains_actionable_after_two_days(google_db, monkeypatch):  # noqa: F811
    from agent_core.graph.runtime import ToolCallRequest
    from agent_core.tools import ToolContext
    from backend.models import AgentApproval, AgentRun, RunStep, ToolCall
    from backend.models.automation import Automation, AutomationRun
    from backend.services import tool_approvals
    from backend.services.automations.dispatch import dispatch_due
    from backend.services.automations.service import get_owned_automation_run
    from backend.services.run_events import current_job

    db = google_db
    view, identity, context = await active(db, monkeypatch)
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
        await session.commit()
        tool_context = ToolContext(db.owner, row.conversation_id, "user", run_id=row.agent_run_id)
    manager = tool_approvals.ToolApprovalManager(timeout_seconds=300)

    async def wait():
        token = current_job.set(context.job)
        try:
            return await manager.wait_durable(
                tool_context,
                str(approval_id),
                ToolCallRequest(
                    "python", "run_python_code", {"code": "print(1)"}, '{"code":"print(1)"}'
                ),
            )
        finally:
            current_job.reset(token)

    task = asyncio.create_task(wait())
    try:
        for _ in range(100):
            async with db.sessions() as session:
                approval = await session.get(AgentApproval, approval_id)
            if approval:
                break
            await asyncio.sleep(0.01)
        assert approval is not None and approval.expires_at is None
        future = datetime.now(UTC) + timedelta(days=2)

        class Clock:
            @classmethod
            def now(cls, tz=None):
                return future

        monkeypatch.setattr(tool_approvals, "datetime", Clock)
        async with db.sessions() as session:
            auto = await session.get(Automation, view.id)
            auto.enabled, auto.next_run_at = True, future
            await session.commit()
            report = await dispatch_due(session, now=future)
            assert report.skipped_overlap == 1 and report.queued == 0
            loaded = await get_owned_automation_run(session, db.owner, identity)
            assert loaded.pending_approvals[0]["expires_at"] is None
        assert await manager.resolve_durable(
            approval_id=approval_id,
            run_id=tool_context.run_id,
            conversation_id=tool_context.conversation_id,
            user_id=db.owner,
            approved=True,
        )
        assert await asyncio.wait_for(task, 3)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_chat_durable_approval_keeps_existing_finite_timeout(google_db, monkeypatch):  # noqa: F811
    from automation_fixtures import runtime
    from test_automation_execution import Model

    from agent_core.graph.runtime import ToolCallRequest
    from agent_core.tools import ToolContext
    from backend.models import AgentApproval, AgentRun
    from backend.services.tool_approvals import ToolApprovalManager

    db = google_db
    runtime(db, monkeypatch, Model())
    async with db.sessions() as session:
        agent = AgentRun(conversation_id=db.conversation, status="waiting_approval")
        session.add(agent)
        await session.commit()
    identity = uuid4()
    result = await ToolApprovalManager(timeout_seconds=0.01).wait_durable(
        ToolContext(db.owner, db.conversation, "user", run_id=agent.id),
        str(identity),
        ToolCallRequest("python", "run_python_code", {"code": "print(1)"}, '{"code":"print(1)"}'),
    )
    assert result is False
    async with db.sessions() as session:
        assert (await session.get(AgentApproval, identity)).expires_at is not None


async def test_stop_during_accepted_google_rpc_keeps_unknown_fence(google_db, monkeypatch):  # noqa: F811
    from dataclasses import replace

    from sqlalchemy import select
    from test_automation_authority import setup
    from test_google_mutations import approved

    from backend.models import GoogleMutation
    from backend.models.automation import AutomationRun
    from backend.schemas.google import SheetChange
    from backend.services.automations.execution import sync_automation_run
    from backend.services.automations.service import request_run_stop

    db = google_db
    service, context, ref = await setup(db, monkeypatch)
    context = replace(context, operation_key="rpc-stop")
    proposal = await service.sheets.prepare(
        context,
        ref,
        SheetChange(kind="append_rows", range_a1="Sheet1!A1:B100", rows=[["rpc-url", "topic"]]),
    )
    entered = asyncio.Event()
    original = db.provider.handle

    async def blocked(request):
        response = await original(request)
        if request.url.path.endswith(":append"):
            entered.set()
            await asyncio.Event().wait()  # Remote write happened; response not received.
        return response

    import httpx

    service.access.credentials.http = type(service.access.credentials.http)(
        httpx.MockTransport(blocked)
    )
    # Approval is an exact host receipt; stop does not invent permission or retry.
    task = asyncio.create_task(
        service.sheets.execute(
            approved(context, proposal), **proposal.commit_arguments()
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with db.sessions() as session:
            run = await session.scalar(
                select(AutomationRun).where(AutomationRun.agent_run_id == context.run_id)
            )
            assert (await request_run_stop(session, db.owner, run.id)).status == "cancelling"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with db.sessions() as session:
            run = await sync_automation_run(session, run.id, drained=True)
            assert run.status == "needs_reconciliation"
            assert (await session.get(GoogleMutation, proposal.id)).status == "unknown"
        assert len(db.provider.writes) == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
