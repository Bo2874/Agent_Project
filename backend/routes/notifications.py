from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from backend.dependencies import CurrentUser, DbSession
from backend.models.notification import UserNotification
from backend.routes.automations import enabled

router = APIRouter(
    prefix="/api/notifications", tags=["notifications"], dependencies=[Depends(enabled)]
)


@router.get("")
async def list_notifications(session: DbSession, user: CurrentUser):
    rows = (
        await session.scalars(
            select(UserNotification)
            .where(UserNotification.owner_id == user.id)
            .order_by(UserNotification.created_at.desc(), UserNotification.id)
            .limit(100)
        )
    ).all()
    count = await session.scalar(
        select(func.count())
        .select_from(UserNotification)
        .where(UserNotification.owner_id == user.id, UserNotification.read_at.is_(None))
    )
    return {
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "result": row.result,
                "read_at": row.read_at,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "unread_count": count,
    }


@router.post("/{identity}/read")
async def mark_read(identity: UUID, session: DbSession, user: CurrentUser):
    row = await session.scalar(
        select(UserNotification)
        .where(UserNotification.id == identity, UserNotification.owner_id == user.id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Notification not found")
    row.read_at = row.read_at or datetime.now(UTC)
    await session.commit()
    return {"id": row.id, "read_at": row.read_at}
