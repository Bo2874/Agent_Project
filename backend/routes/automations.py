from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from backend.core.config import get_settings
from backend.dependencies import CurrentUser, DbSession
from backend.models.automation import ACTIVE_STATUSES, Automation, AutomationRun
from backend.schemas.automation import (
    AutomationDraft,
    AutomationRunView,
    CloseUnknownInput,
    EnabledInput,
    RunNowInput,
    SchedulePreviewInput,
)
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


def google_access():
    from backend.services.google.access import GoogleAccess
    from backend.services.google.credentials import GoogleCredentials

    return GoogleAccess(GoogleCredentials(get_settings()))


@router.get("/inputs")
async def list_inputs(session: DbSession, user: CurrentUser):
    from backend.services.automations.inputs import available_inputs

    return await available_inputs(session, user.id, get_settings())


@router.post("/schedules/preview")
async def preview_schedule(body: SchedulePreviewInput, user: CurrentUser):
    from zoneinfo import ZoneInfoNotFoundError

    from backend.services.automations.schedules import from_local_input, next_occurrence

    try:
        schedule = from_local_input(body)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "schedule": schedule.model_dump(mode="json"),
        "next_run_at": next_occurrence(schedule, after=service.utcnow()),
    }


@router.get("/inputs/google/{connection_id}/{file_id}/tabs")
async def tabs(connection_id: UUID, file_id: str, session: DbSession, user: CurrentUser):
    from backend.services.automations.inputs import selected_tabs
    from backend.services.google.errors import GoogleServiceError

    if not get_settings().google_connector_enabled:
        raise HTTPException(404, "Google connector is disabled")
    try:
        return await selected_tabs(session, user.id, connection_id, file_id, google_access())
    except GoogleServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


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


@router.post("/runs/{run_id}/stop")
async def stop(run_id: UUID, session: DbSession, user: CurrentUser):
    return await service.request_run_stop(session, user.id, run_id)


@router.post("/runs/{run_id}/close-unknown")
async def close_unknown(
    run_id: UUID, body: CloseUnknownInput, session: DbSession, user: CurrentUser
):
    return await service.close_unknown_run(session, user.id, run_id)


@router.post("/{identity}/enabled")
async def toggle(identity: UUID, body: EnabledInput, session: DbSession, user: CurrentUser):
    return await service.set_schedule_enabled(session, user.id, identity, **body.model_dump())


@router.post("/{identity}/run-now")
async def manual_run(identity: UUID, body: RunNowInput, session: DbSession, user: CurrentUser):
    return await service.run_now(session, user.id, identity, idempotency_key=body.idempotency_key)


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
            .order_by(
                AutomationRun.status.in_(ACTIVE_STATUSES).desc(),
                AutomationRun.created_at.desc(),
                AutomationRun.id.desc(),
            )
            .limit(100)
        )
    ).all()
    return {"items": [AutomationRunView.model_validate(row) for row in rows]}
