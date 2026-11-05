"""Authenticated Google account control; browser receives no refresh credentials."""

import logging
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from starlette.responses import JSONResponse, RedirectResponse

from backend.core.config import get_settings
from backend.dependencies import CurrentUser, DbSession
from backend.models import GoogleConnection
from backend.schemas.google import ConnectionView
from backend.services.google.errors import GoogleServiceError
from backend.services.google.oauth import GoogleOAuth


def oauth_service():
    return GoogleOAuth(get_settings())


async def enabled(request: Request, response: Response):
    if not get_settings().google_connector_enabled:
        raise HTTPException(404, "Google connector is disabled")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site writes are not allowed")
        origin = request.headers.get("origin")
        if origin:
            try:
                expected, supplied = urlsplit(str(request.base_url)), urlsplit(origin)
                valid = (expected.scheme, expected.netloc) == (supplied.scheme, supplied.netloc)
                valid &= not bool(
                    supplied.username or supplied.password or supplied.query or supplied.fragment
                )
            except ValueError:
                valid = False
            if not valid:
                raise HTTPException(403, "Cross-origin writes are not allowed")


router = APIRouter(tags=["connections"], dependencies=[Depends(enabled)])


@router.get("/api/connections/google")
async def google_status(session: DbSession, user: CurrentUser):
    rows = await session.scalars(
        select(GoogleConnection).where(GoogleConnection.owner_id == user.id)
    )
    return {
        "configured": oauth_service().configured(),
        "connections": [ConnectionView.model_validate(row).model_dump(mode="json") for row in rows],
    }


@router.post("/api/connections/google")
async def begin_google(session: DbSession, user: CurrentUser):
    return await oauth_service().begin(session, user.id)


@router.delete("/api/connections/google/{connection_id}", status_code=204)
async def disconnect_google(connection_id: UUID, session: DbSession, user: CurrentUser):
    await oauth_service().disconnect(session, user.id, connection_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/connectors/google/callback")
async def google_callback(request: Request, session: DbSession, user: CurrentUser):
    # Parse manually so framework validation responses never reflect OAuth code/state.
    status = "reconnect"
    try:
        if not request.query_params.get("error"):
            view = await oauth_service().complete(
                session,
                user.id,
                state=request.query_params.get("state", ""),
                code=request.query_params.get("code", ""),
            )
            status = view.status
    except GoogleServiceError:
        await session.rollback()
    return RedirectResponse(
        "/connectors?google=" + status,
        status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


class GoogleCallbackLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            if (
                isinstance(args[2], str)
                and args[2].split("?", 1)[0] == "/connectors/google/callback"
            ):
                args[2] = "/connectors/google/callback?[redacted]"
                record.args = tuple(args)
        return True


def install(app):
    app.include_router(router)

    @app.exception_handler(GoogleServiceError)
    async def google_error_handler(_request, error):
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail, "code": error.code},
            headers={"Cache-Control": "no-store"},
        )

    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, GoogleCallbackLogFilter) for item in logger.filters):
        logger.addFilter(GoogleCallbackLogFilter())
