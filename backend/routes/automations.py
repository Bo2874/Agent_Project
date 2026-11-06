from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from backend.core.config import get_settings
from backend.dependencies import CurrentUser, DbSession
from backend.models.automation import Automation, AutomationRun
from backend.schemas.automation import AutomationDraft, AutomationRunView
from backend.services.automations import service


async def enabled(request: Request, response: Response):
    if not get_settings().automations_enabled:
        raise HTTPException(404, "Automations are disabled")
    response.headers["Cache-Control"] = "no-store"
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site writes are not allowed")
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            expected = urlsplit(str(request.base_url))
            if (
                (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc)
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise HTTPException(403, "Cross-origin writes are not allowed")


router = APIRouter(prefix="/api/automations", tags=["automations"], dependencies=[Depends(enabled)])


@router.post("")
async def create(body: AutomationDraft, session: DbSession, user: CurrentUser):
    return await service.create_automation(session, user.id, body)


@router.get("")
async def list_automations(session: DbSession, user: CurrentUser):
    rows = (
        await session.scalars(
            select(Automation)
            .where(Automation.owner_id == user.id)
            .order_by(Automation.updated_at.desc())
            .limit(100)
        )
    ).all()
    return {"items": [await service.view(session, row) for row in rows]}


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, session: DbSession, user: CurrentUser):
    return await service.get_owned_automation_run(session, user.id, run_id)


@router.get("/{identity}")
async def get_automation(identity: UUID, session: DbSession, user: CurrentUser):
    return await service.view(session, await service.owned_automation(session, user.id, identity))


@router.patch("/{identity}")
async def revise(identity: UUID, body: AutomationDraft, session: DbSession, user: CurrentUser):
    return await service.revise_automation(session, user.id, identity, body)


@router.get("/{identity}/runs")
async def list_runs(identity: UUID, session: DbSession, user: CurrentUser):
    await service.owned_automation(session, user.id, identity)
    rows = (
        await session.scalars(
            select(AutomationRun)
            .where(AutomationRun.automation_id == identity, AutomationRun.owner_id == user.id)
            .order_by(AutomationRun.created_at.desc())
            .limit(100)
        )
    ).all()
    return {"items": [AutomationRunView.model_validate(row) for row in rows]}
