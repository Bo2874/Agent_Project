# ruff: noqa: F811
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from google_fixtures import google_db  # noqa: F401
from test_automation_controls import active


@pytest.mark.parametrize("kind", ["artifacts", "documents", "google"])
async def test_execution_conversation_rejects_work_selection_changes(google_db, monkeypatch, kind):
    from fastapi import HTTPException

    from backend.models.automation import AutomationRun
    from backend.routes import artifacts, chat, connections
    from backend.services.google.oauth import GoogleOAuth

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    monkeypatch.setattr(
        connections, "oauth_service", lambda: GoogleOAuth(db.settings, transport=db.transport)
    )
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        actor = SimpleNamespace(id=db.owner)
        with pytest.raises(HTTPException) as failure:
            if kind == "artifacts":
                await artifacts.select_artifacts(
                    run.conversation_id, SimpleNamespace(version_ids=[]), session, actor
                )
            elif kind == "documents":
                await chat.put_selected_documents(
                    run.conversation_id, SimpleNamespace(document_ids=[]), session, actor
                )
            else:
                await connections.choose_work_google_files(
                    run.conversation_id, SimpleNamespace(file_refs=[]), session, actor
                )
        assert failure.value.status_code == 409


async def test_second_approval_has_unread_notice(google_db, monkeypatch):
    from sqlalchemy import select

    from backend.models import RunStep, ToolCall
    from backend.models.automation import AutomationRun
    from backend.models.notification import UserNotification
    from backend.services.automations.notifications import notify_automation_result

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        run.status = "waiting_approval"
        step = RunStep(agent_run_id=run.agent_run_id, step_number=1, status="completed")
        session.add(step)
        await session.flush()
        call = ToolCall(
            run_step_id=step.id,
            tool_name="run_python_code",
            arguments={"code": "first"},
            status="waiting_approval",
            approval_id=uuid4(),
        )
        session.add(call)
        await session.commit()
        await notify_automation_result(session, run.id)
        first = await session.scalar(select(UserNotification))
        first.read_at = datetime.now(UTC)
        call.status = "completed"
        session.add(
            ToolCall(
                run_step_id=step.id,
                tool_name="run_python_code",
                arguments={"code": "second"},
                status="waiting_approval",
                approval_id=uuid4(),
            )
        )
        await session.commit()
        await notify_automation_result(session, run.id)
        await notify_automation_result(session, run.id)  # Delivery replay is idempotent.
        notices = (await session.scalars(select(UserNotification))).all()
        assert len(notices) == 2 and sum(n.read_at is None for n in notices) == 1


async def test_old_active_run_remains_listed(google_db, monkeypatch):
    from backend.models.automation import AutomationRun
    from backend.routes.automations import list_runs

    db = google_db
    view, identity, _ = await active(db, monkeypatch)
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        run.created_at = datetime.now(UTC) - timedelta(days=200)
        run.status = "needs_reconciliation"
        for i in range(101):
            session.add(
                AutomationRun(
                    owner_id=db.owner,
                    automation_id=view.id,
                    revision_id=run.revision_id,
                    trigger="scheduled",
                    scheduled_for=datetime.now(UTC) - timedelta(days=i),
                    status="skipped_overlap",
                )
            )
        await session.commit()
        response = await list_runs(view.id, session, SimpleNamespace(id=db.owner))
        assert any(item.id == identity for item in response["items"])


@pytest.mark.parametrize("stop", [True, False])
@pytest.mark.parametrize("unknown", [False, True])
async def test_terminal_lease_recovery_releases_automation_state(
    google_db, monkeypatch, stop, unknown
):
    from backend.models import AgentOperation, BackgroundJob
    from backend.models.automation import AutomationRun
    from backend.services.automations.service import request_run_stop

    db = google_db
    _, identity, context = await active(db, monkeypatch)
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        if unknown:
            session.add(
                AgentOperation(
                    agent_run_id=run.agent_run_id,
                    key="root.model.uncertain",
                    fingerprint="fixture",
                    status="started",
                )
            )
        if stop:
            await request_run_stop(session, db.owner, identity)
        job = await session.get(BackgroundJob, run.job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
        if not stop:
            job.attempt_count = job.max_attempts
        await session.commit()
    await context.queue.recover_expired_leases(queue_name="default")
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        assert run.status == (
            "needs_reconciliation" if unknown else "cancelled" if stop else "failed"
        )
    await context.queue.recover_expired_leases(queue_name="default")


@pytest.mark.parametrize("status", ["cancelled", "failed"])
async def test_terminal_projection_is_retried_after_queue_commit(google_db, monkeypatch, status):
    from backend.models import BackgroundJob
    from backend.models.automation import AutomationRun

    db = google_db
    _, identity, context = await active(db, monkeypatch)
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        job = await session.get(BackgroundJob, row.job_id)
        job.status, job.lease_expires_at = status, None
        if status == "cancelled":
            row.status = "cancelling"
        await session.commit()  # A killed process never got to update the projections.
    assert await context.queue.recover_expired_leases(queue_name="default") == 0
    async with db.sessions() as session:
        assert (await session.get(AutomationRun, identity)).status == status


async def test_terminal_lease_before_agent_admission_releases_slot(google_db, monkeypatch):
    from automation_fixtures import queued, runtime
    from test_automation_execution import Model

    from backend.background.queue import PostgresJobQueue
    from backend.models import BackgroundJob
    from backend.models.automation import AutomationRun

    db = google_db
    runtime(db, monkeypatch, Model())
    _, identity = await queued(db)
    queue = PostgresJobQueue()
    claim = await queue.claim_next(
        queue_name="default", worker_id="before-admission", lease_seconds=1
    )
    async with db.sessions() as session:
        job = await session.get(BackgroundJob, claim.id)
        job.attempt_count = job.max_attempts
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    await queue.recover_expired_leases(queue_name="default")
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        assert run.status == "failed" and run.agent_run_id is None and run.finished_at


async def test_replayed_paid_tool_usage_is_not_counted_twice(google_db, monkeypatch):
    from agent_core.graph import AgentLoopEvent, ToolCallRequest
    from agent_core.llm.types import TokenUsage
    from agent_core.tools import ToolResult
    from backend.models import AgentRun
    from backend.models.automation import AutomationRun
    from backend.services.agent_runs import AgentRunRecorder, list_agent_run_timelines

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        agent = await session.get(AgentRun, run.agent_run_id)
    kwargs = dict(
        agent_run_id=agent.id,
        conversation_id=agent.conversation_id,
        user_message_id=agent.user_message_id,
        provider="fixture",
        model_name="fixture",
    )
    request = ToolCallRequest("lookup", "search_web", {"query": "fixture"}, "{}")
    result = ToolResult.success(
        "result", data={"usage": {"total_tokens": 120}}, usage=TokenUsage(total_tokens=120)
    )
    for replay in (False, True):
        recorder = AgentRunRecorder(**kwargs)
        await recorder(AgentLoopEvent(kind="model_started", step_number=1))
        await recorder(
            AgentLoopEvent(
                kind="model_completed",
                step_number=1,
                finish_reason="tool_calls",
                usage=TokenUsage(total_tokens=0 if replay else 100),
            )
        )
        await recorder(AgentLoopEvent(kind="tool_started", step_number=1, tool_call=request))
        await recorder(
            AgentLoopEvent(
                kind="tool_completed",
                step_number=1,
                tool_call=request,
                tool_result=result,
                usage=TokenUsage(total_tokens=0 if replay else 120),
            )
        )
    async with db.sessions() as session:
        timelines = await list_agent_run_timelines(session, agent.conversation_id)
        assert timelines[0].total_tokens == 220


@pytest.mark.parametrize("react", [False, True])
async def test_execution_conversation_rejects_new_chat_message(google_db, monkeypatch, react):
    from fastapi import HTTPException
    from sqlalchemy import func, select
    from test_automation_execution import Model

    from agent_core.skills import SkillRegistry
    from agent_core.tools import ToolRegistry
    from backend.models import Message, User
    from backend.models.automation import AutomationRun
    from backend.routes import chat
    from backend.schemas.chat import MessageCreate

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    settings = chat.get_settings()
    settings.agent_react_enabled = react
    from langgraph.checkpoint.memory import InMemorySaver

    monkeypatch.setattr(chat, "get_langgraph_checkpointer", InMemorySaver)
    model = Model()
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        user = await session.get(User, db.owner)
        before = await session.scalar(select(func.count(Message.id)))
        with pytest.raises(HTTPException) as failure:
            await chat.post_message(
                row.conversation_id,
                MessageCreate(content="extra request"),
                session,
                user,
                model,
                ToolRegistry(),
                SkillRegistry(),
                None,
            )
        assert failure.value.status_code == 409
        assert await session.scalar(select(func.count(Message.id))) == before
    assert model.messages == []


async def test_synthetic_automation_instruction_does_not_extract_personal_memory(
    google_db, monkeypatch
):
    from unittest.mock import AsyncMock

    from automation_fixtures import queued, runtime
    from test_automation_execution import Model

    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.routes import chat

    db = google_db
    settings, _ = runtime(db, monkeypatch, Model())
    settings.memory_extraction_enabled = True
    extraction = AsyncMock(return_value=SimpleNamespace(total_tokens=70))
    monkeypatch.setattr(chat, "extract_and_store_user_message_memories", extraction)
    await queued(db)
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="memory-review",
    )
    assert await worker.run_once()
    extraction.assert_not_awaited()


async def test_waiting_approval_projection_does_not_depend_on_callback_order(
    google_db, monkeypatch
):
    from automation_fixtures import queued, runtime
    from sqlalchemy import select

    import backend.dependencies_tools as tools
    from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
    from agent_core.tools import ToolExecutionPolicy, ToolRegistry, create_context_tool
    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.models import AgentApproval, AgentRun
    from backend.models.automation import AutomationRun
    from backend.services.agent_runs import AgentRunRecorder
    from backend.services.automations.service import request_run_stop

    class Model:
        provider, model, max_tokens = "fixture", "approval-order", 128

        async def stream(self, messages, **kwargs):
            yield LLMChunk(
                finish_reason="tool_calls",
                usage=TokenUsage(total_tokens=10),
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0, id="raw", name="run_python_code", arguments='{"code":"print(1)"}'
                    ),
                ),
            )

        async def aclose(self):
            pass

    db = google_db
    runtime(db, monkeypatch, Model())

    async def forbidden(*args):
        pytest.fail("No tool should execute while approval is pending")

    registry = ToolRegistry()
    registry.register(
        create_context_tool(
            name="run_python_code",
            description="fixture",
            coroutine=forbidden,
            args_schema={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        ),
        policy=ToolExecutionPolicy(requires_approval=True),
    )
    monkeypatch.setattr(tools, "get_tool_registry", lambda: registry)
    release = asyncio.Event()
    delayed = asyncio.Event()
    original = AgentRunRecorder._record_tool_approval_required

    async def delay(self, event):
        delayed.set()
        await release.wait()
        await original(self, event)

    monkeypatch.setattr(AgentRunRecorder, "_record_tool_approval_required", delay)
    _, identity = await queued(db)
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="approval-order",
        control_poll_seconds=0.1,
    )
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(delayed.wait(), 5)
        for _ in range(100):
            async with db.sessions() as session:
                pending = await session.scalar(select(AgentApproval.id))
            if pending:
                break
            await asyncio.sleep(0.01)
        assert pending
        release.set()
        for _ in range(100):
            async with db.sessions() as session:
                row = await session.get(AutomationRun, identity)
                agent = await session.get(AgentRun, row.agent_run_id)
            if row.status == "waiting_approval":
                break
            await asyncio.sleep(0.01)
        assert row.status == "waiting_approval"
        assert agent.status == "waiting_approval"
    finally:
        release.set()
        async with db.sessions() as session:
            await request_run_stop(session, db.owner, identity)
        await asyncio.wait_for(task, 5)
