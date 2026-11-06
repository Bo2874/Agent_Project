from uuid import uuid4

import pytest
from google_fixtures import google_db  # noqa: F401
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize(
    "status", ["queued", "running", "waiting_approval", "cancelling", "needs_reconciliation"]
)
async def test_waiting_approval_and_unknown_run_are_active(google_db, status):  # noqa: F811
    from backend.models.automation import AutomationRun
    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.service import create_automation

    db = google_db
    async with db.sessions() as session:
        auto = await create_automation(
            session,
            db.owner,
            AutomationDraft(
                name="test",
                instruction="hello",
                schedule={"kind": "daily", "timezone": "Asia/Ho_Chi_Minh", "local_time": "08:00"},
            ),
        )
        first = AutomationRun(
            owner_id=db.owner,
            automation_id=auto.id,
            revision_id=auto.revision_id,
            trigger="manual",
            status=status,
            idempotency_key="first",
        )
        session.add(first)
        await session.commit()
        session.add(
            AutomationRun(
                owner_id=db.owner,
                automation_id=auto.id,
                revision_id=auto.revision_id,
                trigger="manual",
                status="queued",
                idempotency_key="second",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_revision_cannot_reference_another_owners_automation(google_db):  # noqa: F811
    from backend.models.automation import Automation, AutomationRevision

    db = google_db
    async with db.sessions() as session:
        auto = Automation(id=uuid4(), owner_id=db.owner, name="owned")
        session.add(auto)
        await session.commit()
        session.add(
            AutomationRevision(
                automation_id=auto.id,
                owner_id=db.other,
                number=1,
                instruction="forged",
                schedule={},
                artifact_version_ids=[],
                artifact_actions=[],
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_notification_event_key_is_unique_per_owner(google_db):  # noqa: F811
    from backend.models.notification import UserNotification

    db = google_db
    async with db.sessions() as session:
        session.add_all(
            [
                UserNotification(owner_id=identity, event_key="same-event", title="Done", result={})
                for identity in (db.owner, db.other)
            ]
        )
        await session.commit()
        session.add(
            UserNotification(owner_id=db.owner, event_key="same-event", title="Again", result={})
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
