"""A scheduler publishes jobs atomically; it never runs models or tools."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from backend.background.queue import PostgresJobQueue
from backend.core.config import get_settings
from backend.models import User
from backend.models.automation import ACTIVE_STATUSES, Automation, AutomationRun
from backend.schemas.automation import ScheduleSpec
from backend.services.automations.schedules import missed_occurrences, next_occurrence
from backend.services.automations.service import current_revision


@dataclass
class DispatchReport:
    queued: int = 0
    missed: int = 0
    skipped_overlap: int = 0


async def enqueue_automation_run(
    session, auto, revision, *, trigger, scheduled_for=None, idempotency_key=None
):
    row = AutomationRun(
        owner_id=auto.owner_id,
        automation_id=auto.id,
        revision_id=revision.id,
        trigger=trigger,
        scheduled_for=scheduled_for,
        idempotency_key=idempotency_key,
        status="queued",
    )
    session.add(row)
    await session.flush()
    job = await PostgresJobQueue().enqueue(
        job_type="automation.run",
        payload={"automation_run_id": str(row.id)},
        queue_name=get_settings().background_queue_name,
        user_id=auto.owner_id,
        max_attempts=3,
        idempotency_key=f"automation-{row.id}",
        session=session,
    )
    row.job_id = job.id
    return row


async def dispatch_due(session, *, now: datetime, limit: int = 100) -> DispatchReport:
    if now.utcoffset() is None or not 1 <= limit <= 100:
        raise ValueError("Use an aware clock and a batch size from 1 to 100")
    now = now.astimezone(UTC)
    settings = get_settings()
    report = DispatchReport()
    rows = (
        await session.scalars(
            select(Automation)
            .join(User, User.id == Automation.owner_id)
            .where(
                Automation.enabled.is_(True),
                Automation.next_run_at <= now,
                User.is_active.is_(True),
            )
            .order_by(Automation.next_run_at, Automation.id)
            .limit(limit)
            .with_for_update(of=Automation, skip_locked=True)
        )
    ).all()
    for auto in rows:
        revision = await current_revision(session, auto)
        spec = ScheduleSpec.model_validate(revision.schedule)
        first = auto.next_run_at
        active = await session.scalar(
            select(AutomationRun.id)
            .where(
                AutomationRun.automation_id == auto.id, AutomationRun.status.in_(ACTIVE_STATUSES)
            )
            .limit(1)
        )
        if (now - first).total_seconds() > settings.automation_schedule_grace_seconds:
            count = missed_occurrences(spec, first, now)
            session.add(
                AutomationRun(
                    owner_id=auto.owner_id,
                    automation_id=auto.id,
                    revision_id=revision.id,
                    trigger="scheduled",
                    scheduled_for=first,
                    status="missed",
                    finished_at=now,
                    result={"missed_count": count, "through": now.isoformat()},
                )
            )
            report.missed += count
        elif active is not None:
            session.add(
                AutomationRun(
                    owner_id=auto.owner_id,
                    automation_id=auto.id,
                    revision_id=revision.id,
                    trigger="scheduled",
                    scheduled_for=first,
                    status="skipped_overlap",
                    finished_at=now,
                )
            )
            report.skipped_overlap += 1
        else:
            await enqueue_automation_run(
                session, auto, revision, trigger="scheduled", scheduled_for=first
            )
            report.queued += 1
        auto.next_run_at = next_occurrence(spec, after=now)
    await session.commit()
    return report
