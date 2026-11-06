import asyncio
from datetime import UTC, datetime, timedelta

from google_fixtures import google_db  # noqa: F401
from sqlalchemy import select
from test_automation_api import draft


async def due(db, now):
    from backend.models.automation import Automation
    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.service import create_automation

    async with db.sessions() as session:
        view = await create_automation(session, db.owner, AutomationDraft.model_validate(draft()))
        auto = await session.get(Automation, view.id)
        auto.enabled, auto.next_run_at = True, now
        await session.commit()
        return view


async def test_two_schedulers_dispatch_only_one_durable_job(google_db):  # noqa: F811
    from backend.models import BackgroundJob
    from backend.models.automation import Automation, AutomationRun
    from backend.services.automations.dispatch import dispatch_due

    db = google_db
    now = datetime(2026, 11, 7, 1, tzinfo=UTC)
    view = await due(db, now)

    async def tick():
        async with db.sessions() as session:
            return await dispatch_due(session, now=now)

    await asyncio.gather(tick(), tick())
    async with db.sessions() as session:
        runs = (
            await session.scalars(
                select(AutomationRun).where(AutomationRun.automation_id == view.id)
            )
        ).all()
        jobs = (
            await session.scalars(
                select(BackgroundJob).where(BackgroundJob.job_type == "automation.run")
            )
        ).all()
        assert len(runs) == len(jobs) == 1
        assert runs[0].job_id == jobs[0].id and runs[0].scheduled_for == now
        assert (await session.get(Automation, view.id)).next_run_at == datetime(
            2026, 11, 8, 1, tzinfo=UTC
        )


async def test_offline_misses_are_recorded_without_backfill(google_db):  # noqa: F811
    from backend.models import BackgroundJob
    from backend.models.automation import Automation, AutomationRun
    from backend.services.automations.dispatch import dispatch_due

    db = google_db
    first = datetime(2026, 11, 5, 1, tzinfo=UTC)
    view = await due(db, first)
    async with db.sessions() as session:
        await dispatch_due(session, now=first + timedelta(days=2, minutes=2))
        run = await session.scalar(
            select(AutomationRun).where(AutomationRun.automation_id == view.id)
        )
        assert run.status == "missed" and run.result["missed_count"] == 3
        assert (
            await session.scalar(
                select(BackgroundJob.id).where(BackgroundJob.job_type == "automation.run")
            )
            is None
        )
        assert (await session.get(Automation, view.id)).next_run_at == datetime(
            2026, 11, 8, 1, tzinfo=UTC
        )


async def test_waiting_approval_skips_next_tick_instead_of_queueing(google_db):  # noqa: F811
    from backend.models.automation import Automation, AutomationRun
    from backend.services.automations.dispatch import dispatch_due

    db = google_db
    now = datetime(2026, 11, 7, 1, tzinfo=UTC)
    view = await due(db, now)
    async with db.sessions() as session:
        session.add(
            AutomationRun(
                owner_id=db.owner,
                automation_id=view.id,
                revision_id=view.revision_id,
                trigger="manual",
                idempotency_key="manual",
                status="waiting_approval",
            )
        )
        await session.commit()
        await dispatch_due(session, now=now)
        statuses = (
            await session.scalars(
                select(AutomationRun.status).where(AutomationRun.automation_id == view.id)
            )
        ).all()
        assert set(statuses) == {"waiting_approval", "skipped_overlap"}
        assert (await session.get(Automation, view.id)).next_run_at > now
