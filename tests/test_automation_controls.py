import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from automation_fixtures import queued, runtime
from fastapi import HTTPException
from google_fixtures import google_db  # noqa: F401
from langgraph.checkpoint.memory import InMemorySaver
from test_automation_execution import Model


async def active(db, monkeypatch):
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import JobExecutionContext
    from backend.services.automations.execution import admit_automation_run

    runtime(db, monkeypatch, Model())
    view, identity = await queued(db)
    queue = PostgresJobQueue()
    claim = await queue.claim_next(queue_name="default", worker_id="control-test", lease_seconds=60)
    context = JobExecutionContext(queue, claim)
    async with db.sessions() as session:
        await admit_automation_run(session, context, identity)
    return view, identity, context


async def test_stop_current_run_keeps_schedule_and_waits_for_drain(google_db, monkeypatch):  # noqa: F811
    from backend.models import BackgroundJob
    from backend.models.automation import Automation
    from backend.services.automations.execution import sync_automation_run
    from backend.services.automations.service import request_run_stop, set_schedule_enabled

    db = google_db
    view, identity, context = await active(db, monkeypatch)
    async with db.sessions() as session:
        await set_schedule_enabled(session, db.owner, view.id, enabled=True)
        stopping = await request_run_stop(session, db.owner, identity)
        assert stopping.status == "cancelling"
        assert (await session.get(Automation, view.id)).enabled
        assert (await session.get(BackgroundJob, stopping.job_id)).cancel_requested_at is not None
        result = await sync_automation_run(session, identity, drained=True, claimed=context.job)
        assert result.status == "cancelled"
        assert result.stop_reason == "user_cancelled"
        assert (await session.get(Automation, view.id)).enabled


async def test_pause_only_keeps_active_and_pause_stop_requests_cancellation(google_db, monkeypatch):  # noqa: F811
    from backend.models.automation import AutomationRun
    from backend.services.automations.service import set_schedule_enabled

    db = google_db
    view, identity, _ = await active(db, monkeypatch)
    async with db.sessions() as session:
        await set_schedule_enabled(session, db.owner, view.id, enabled=False)
        assert (await session.get(AutomationRun, identity)).status == "running"
        await set_schedule_enabled(session, db.owner, view.id, enabled=False, stop_active=True)
        await session.refresh(await session.get(AutomationRun, identity))
        assert (await session.get(AutomationRun, identity)).status == "cancelling"


async def test_pause_wins_before_worker_admission_and_cancels_queued_tick(google_db, monkeypatch):  # noqa: F811
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import JobExecutionContext
    from backend.models.automation import Automation, AutomationRun
    from backend.services.automations.execution import admit_automation_run
    from backend.services.automations.service import owned_automation, set_schedule_enabled

    db = google_db
    runtime(db, monkeypatch, Model())
    view, identity = await queued(db, trigger="scheduled")
    queue = PostgresJobQueue()
    claimed = await queue.claim_next(
        queue_name="default", worker_id="control-test", lease_seconds=60
    )
    async with db.sessions() as pausing:
        auto = await owned_automation(pausing, db.owner, view.id, lock=True)
        auto.enabled = True
        await pausing.flush()
        entered = asyncio.Event()

        async def admit():
            async with db.sessions() as session:
                entered.set()
                return await admit_automation_run(
                    session, JobExecutionContext(queue, claimed), identity
                )

        task = asyncio.create_task(admit())
        await entered.wait()
        await set_schedule_enabled(pausing, db.owner, view.id, enabled=False)
        result = await asyncio.wait_for(task, 5)
        assert result.status == "cancelled" and result.agent_run_id is None
    async with db.sessions() as session:
        assert not (await session.get(Automation, view.id)).enabled
        assert (await session.get(AutomationRun, identity)).status == "cancelled"


async def test_manual_run_disabled_is_idempotent_and_second_active_conflicts(google_db):  # noqa: F811
    from test_automation_api import draft

    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.service import create_automation, run_now

    db = google_db
    async with db.sessions() as session:
        view = await create_automation(session, db.owner, AutomationDraft.model_validate(draft()))
        first = await run_now(session, db.owner, view.id, idempotency_key="same-click")
        again = await run_now(session, db.owner, view.id, idempotency_key="same-click")
        assert first.id == again.id and first.trigger == "manual"
        with pytest.raises(HTTPException) as error:
            await run_now(session, db.owner, view.id, idempotency_key="second-click")
        assert error.value.status_code == 409
        await session.rollback()
        with pytest.raises(HTTPException) as error:
            await run_now(session, db.other, view.id, idempotency_key="foreign")
        assert error.value.status_code == 404


async def test_reenable_schedules_future_instead_of_backfilling(google_db, monkeypatch):  # noqa: F811
    from backend.models.automation import Automation
    from backend.services.automations import service

    db = google_db
    view, identity = await queued(db)
    clock = datetime(2026, 11, 7, 2, tzinfo=UTC)
    monkeypatch.setattr(service, "utcnow", lambda: clock, raising=False)
    async with db.sessions() as session:
        auto = await session.get(Automation, view.id)
        auto.next_run_at = clock - timedelta(days=2)
        await session.commit()
        result = await service.set_schedule_enabled(session, db.owner, view.id, enabled=True)
        assert result.next_run_at == datetime(2026, 11, 8, 1, tzinfo=UTC)


async def test_close_unknown_releases_slot_without_replaying_google(google_db, monkeypatch):  # noqa: F811
    from sqlalchemy import select
    from test_automation_authority import setup
    from test_google_docs import approved

    from backend.models import GoogleMutation
    from backend.models.automation import AutomationRun
    from backend.schemas.google import SheetChange
    from backend.services.automations.execution import sync_automation_run
    from backend.services.automations.service import close_unknown_run
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    service, context, ref = await setup(db, monkeypatch)
    proposal = await service.sheets.prepare(
        context,
        ref,
        SheetChange(kind="append_rows", range_a1="Sheet1!A1:B100", rows=[["url", "topic"]]),
    )
    db.provider.lose_write_response = True
    with pytest.raises(GoogleServiceError):
        await service.sheets.execute(
            approved(context, proposal, "google_sheets_append"), **proposal.commit_arguments()
        )
    async with db.sessions() as session:
        row = await session.scalar(
            select(AutomationRun).where(AutomationRun.agent_run_id == context.run_id)
        )
        await sync_automation_run(session, row.id, drained=True)
        assert row.status == "needs_reconciliation"
        with pytest.raises(HTTPException) as error:
            await close_unknown_run(session, db.other, row.id)
        assert error.value.status_code == 404
        result = await close_unknown_run(session, db.owner, row.id)
        assert result.status == "failed" and result.stop_reason == "closed_without_replay"
        mutation = await session.get(GoogleMutation, proposal.id)
        assert mutation.status == "unknown" and result.result["usage_unknown"]
    assert len(db.provider.writes) == 1


async def test_late_approval_and_chat_resume_do_not_revive_stopped_run(google_db, monkeypatch):  # noqa: F811
    from uuid import uuid4

    from backend.models import AgentApproval, AgentRun
    from backend.models.automation import AutomationRun
    from backend.services.agent_runs import AgentRunResumeError, prepare_agent_run_resume
    from backend.services.automations.service import request_run_stop
    from backend.services.tool_approvals import tool_approval_manager

    db = google_db
    _, identity, _ = await active(db, monkeypatch)
    approval_id = uuid4()
    async with db.sessions() as session:
        row = await session.get(AutomationRun, identity)
        agent = await session.get(AgentRun, row.agent_run_id)
        agent.status, row.status = "waiting_approval", "waiting_approval"
        session.add(
            AgentApproval(
                id=approval_id,
                agent_run_id=agent.id,
                fingerprint="fixture",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await session.commit()
        await request_run_stop(session, db.owner, row.id)
        assert not await tool_approval_manager.resolve_durable(
            approval_id=approval_id,
            run_id=agent.id,
            conversation_id=row.conversation_id,
            user_id=db.owner,
            approved=True,
        )
        agent.status = "failed"
        await session.commit()
        with pytest.raises(AgentRunResumeError, match="Automation"):
            await prepare_agent_run_resume(
                session,
                agent_run_id=agent.id,
                conversation_id=row.conversation_id,
                checkpointer=InMemorySaver(),
            )


async def test_completed_google_write_remains_visible_after_stop(google_db, monkeypatch):  # noqa: F811
    from sqlalchemy import select
    from test_automation_authority import setup
    from test_google_docs import approved

    from backend.models import GoogleMutation
    from backend.models.automation import AutomationRun
    from backend.schemas.google import SheetChange
    from backend.services.automations.execution import sync_automation_run
    from backend.services.automations.service import request_run_stop

    db = google_db
    service, context, ref = await setup(db, monkeypatch)
    proposal = await service.sheets.prepare(
        context,
        ref,
        SheetChange(kind="append_rows", range_a1="Sheet1!A1:B100", rows=[["url", "topic"]]),
    )
    receipt = await service.sheets.execute(
        approved(context, proposal, "google_sheets_append"), **proposal.commit_arguments()
    )
    async with db.sessions() as session:
        row = await session.scalar(
            select(AutomationRun).where(AutomationRun.agent_run_id == context.run_id)
        )
        await request_run_stop(session, db.owner, row.id)
        await sync_automation_run(session, row.id, drained=True)
        assert row.result["google_results"][0]["url"] == receipt.url
        assert (await session.get(GoogleMutation, proposal.id)).status == "completed"
    assert len(db.provider.writes) == 1
