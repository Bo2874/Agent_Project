import pytest
from automation_fixtures import queued, runtime
from google_fixtures import google_db  # noqa: F401
from sqlalchemy import select

from agent_core.llm.types import LLMChunk, TokenUsage


class Model:
    provider, model, max_tokens = "fixture", "automation-fixture", 100

    def __init__(self):
        self.messages = []

    async def stream(self, messages, **kwargs):
        self.messages.append(messages)
        yield LLMChunk(
            content="Report complete", finish_reason="stop", usage=TokenUsage(total_tokens=30_000)
        )

    async def aclose(self):
        pass


async def test_manual_run_executes_shared_graph_while_schedule_disabled(google_db, monkeypatch):  # noqa: F811
    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.models import AgentOperation, AgentRun, Conversation, Message
    from backend.models.automation import AutomationRun
    from backend.services.chat import list_conversations

    db, model = google_db, Model()
    runtime(db, monkeypatch, model)
    view, identity = await queued(db)
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="automation-test",
        control_poll_seconds=0.1,
    )
    assert await worker.run_once()
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        assert run.status == "succeeded", run.result
        assert run.result["total_tokens"] == 30_000
        agent = await session.get(AgentRun, run.agent_run_id)
        assert agent.status == "completed"
        conversation = await session.get(Conversation, run.conversation_id)
        assert conversation.source_kind == "automation"
        assert len(await list_conversations(session, db.owner)) == 1  # Original chat only.
        assert (
            await session.scalar(
                select(Message.content).where(
                    Message.conversation_id == conversation.id, Message.role == "assistant"
                )
            )
            == "Report complete"
        )
        saved = await session.scalar(
            select(AgentOperation).where(
                AgentOperation.agent_run_id == agent.id, AgentOperation.key == "input"
            )
        )
        assert saved.result["limits"]["max_total_tokens"] is None
        assert saved.result["model_messages"][-1]["content"] == "Summarize selected inputs"
    assert len(model.messages) == 1


async def test_disabled_scheduled_job_does_not_start_agent(google_db, monkeypatch):  # noqa: F811
    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.models.automation import AutomationRun

    db, model = google_db, Model()
    runtime(db, monkeypatch, model)
    _, identity = await queued(db, trigger="scheduled")
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="automation-test",
    )
    assert await worker.run_once()
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        assert run.status == "cancelled" and run.agent_run_id is None
    assert model.messages == []


async def test_revision_change_does_not_replace_queued_instruction(google_db, monkeypatch):  # noqa: F811
    from test_automation_api import draft

    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.service import revise_automation

    db, model = google_db, Model()
    runtime(db, monkeypatch, model)
    view, _ = await queued(db)
    async with db.sessions() as session:
        await revise_automation(
            session,
            db.owner,
            view.id,
            AutomationDraft.model_validate(draft(instruction="NEW INSTRUCTION")),
        )
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="automation-test",
    )
    assert await worker.run_once()
    assert "Summarize selected inputs" in str(model.messages)
    assert "NEW INSTRUCTION" not in str(model.messages)


async def test_unknown_write_maps_to_reconciliation_even_when_agent_failed(google_db):  # noqa: F811

    from backend.models import AgentRun, Conversation
    from backend.models.automation import AutomationRun
    from backend.services.automations.execution import sync_automation_run

    db = google_db
    _, identity = await queued(db)
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        conversation = Conversation(user_id=db.owner, source_kind="automation")
        session.add(conversation)
        await session.flush()
        agent = AgentRun(
            conversation_id=conversation.id, status="failed", stop_reason="agent_operation_unknown"
        )
        session.add(agent)
        await session.flush()
        row.agent_run_id, row.conversation_id, row.status = agent.id, conversation.id, "running"
        await session.commit()
        await sync_automation_run(session, row.id, drained=True)
        assert row.status == "needs_reconciliation" and row.finished_at is None


@pytest.mark.parametrize(
    "reason,want",
    [("partial_unknown_billing", "needs_reconciliation"), ("partial_token_limit", "failed")],
)
async def test_partial_answer_does_not_report_success(google_db, reason, want):  # noqa: F811
    from backend.models import AgentRun, Conversation
    from backend.models.automation import AutomationRun
    from backend.services.automations.execution import sync_automation_run

    db = google_db
    _, identity = await queued(db)
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        chat = Conversation(user_id=db.owner, source_kind="automation")
        session.add(chat)
        await session.flush()
        agent = AgentRun(conversation_id=chat.id, status="completed", stop_reason=reason)
        session.add(agent)
        await session.flush()
        row.agent_run_id, row.conversation_id, row.status = agent.id, chat.id, "running"
        await session.commit()
        await sync_automation_run(session, row.id, drained=True)
        assert row.status == want


async def test_execution_conversation_cannot_be_deleted_through_chat_controls(google_db):  # noqa: F811
    from langgraph.checkpoint.memory import InMemorySaver

    from backend.models import Conversation
    from backend.services.chat import ConversationBusyError, delete_conversation

    db = google_db
    async with db.sessions() as session:
        row = Conversation(user_id=db.owner, source_kind="automation")
        session.add(row)
        await session.commit()
        with pytest.raises(ConversationBusyError):
            await delete_conversation(
                session, conversation_id=row.id, user_id=db.owner, checkpointer=InMemorySaver()
            )


async def test_revoked_input_before_admission_releases_run_slot(google_db, monkeypatch):  # noqa: F811
    from test_automation_api import draft
    from test_google_file_access import selected

    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.models.automation import AutomationRun

    db, model = google_db, Model()
    access, _, ref = await selected(db)
    runtime(db, monkeypatch, model)
    _, identity = await queued(
        db,
        body=draft(
            grants=[
                {
                    "connection_id": ref.connection_id,
                    "file_id": ref.file_id,
                    "action": "read_sheet",
                    "range_a1": "Sheet1!A1:B100",
                    "sheet_id": 0,
                }
            ]
        ),
    )
    async with db.sessions() as session:
        await access.revoke_file(session, db.owner, ref.connection_id, ref.file_id)
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="automation-test",
    )
    assert await worker.run_once()
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        assert row.status == "failed" and row.finished_at is not None
        assert row.agent_run_id is None
    assert model.messages == []


async def test_replaced_worker_cannot_finalize_automation_projection(google_db, monkeypatch):  # noqa: F811
    import asyncio
    from datetime import UTC, datetime, timedelta

    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.models import BackgroundJob
    from backend.models.automation import AutomationRun

    entered, release = asyncio.Event(), asyncio.Event()

    class BlockingModel(Model):
        async def stream(self, messages, **kwargs):
            entered.set()
            await release.wait()
            async for chunk in super().stream(messages, **kwargs):
                yield chunk

    db, model = google_db, BlockingModel()
    runtime(db, monkeypatch, model)
    _, identity = await queued(db)
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="old-worker",
        control_poll_seconds=10,
        lease_seconds=60,
    )
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with db.sessions() as session:
            row = await session.get(AutomationRun, identity)
            job = await session.get(BackgroundJob, row.job_id)
            job.worker_id, job.attempt_count = "replacement-worker", job.attempt_count + 1
            job.started_at, job.lease_expires_at = (
                datetime.now(UTC),
                datetime.now(UTC) + timedelta(seconds=60),
            )
            await session.commit()
        release.set()
        await asyncio.wait_for(task, 5)
        async with db.sessions() as session:
            assert (await session.get(AutomationRun, identity)).status == "running"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
