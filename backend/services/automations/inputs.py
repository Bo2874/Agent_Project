"""Configuration metadata for user-selected files, never a whole-Drive search."""

from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select

from backend.models import ArtifactVersion, GoogleConnection, GoogleFileGrant
from backend.schemas.google import SHEET_MIME, ConnectionView
from backend.services.artifacts.service import published
from backend.services.google.access import grant_view
from backend.services.google.client import SHEETS_URL
from backend.services.google.errors import GoogleServiceError


async def available_inputs(session, owner, settings):
    connections, files, artifacts = [], [], []
    if settings.google_connector_enabled:
        connections = (
            await session.scalars(
                select(GoogleConnection).where(
                    GoogleConnection.owner_id == owner,
                    GoogleConnection.status == "connected",
                    GoogleConnection.offline_ready.is_(True),
                )
            )
        ).all()
        if connections:
            files = (
                await session.scalars(
                    select(GoogleFileGrant)
                    .join(GoogleConnection, GoogleConnection.id == GoogleFileGrant.connection_id)
                    .where(
                        GoogleFileGrant.owner_id == owner,
                        GoogleFileGrant.connection_id.in_([row.id for row in connections]),
                        GoogleFileGrant.revoked.is_(False),
                        GoogleFileGrant.connection_generation == GoogleConnection.generation,
                    )
                    .order_by(GoogleFileGrant.name, GoogleFileGrant.id)
                    .limit(100)
                )
            ).all()
    if settings.workspace_artifacts_enabled:
        artifacts = (
            await session.scalars(
                select(ArtifactVersion)
                .where(ArtifactVersion.owner_user_id == owner)
                .order_by(ArtifactVersion.created_at.desc(), ArtifactVersion.id)
                .limit(100)
            )
        ).all()
    return {
        "connections": [
            ConnectionView.model_validate(row).model_dump(mode="json") for row in connections
        ],
        "google_files": [grant_view(row) for row in files],
        "artifacts": [
            {**published(row).model_dump(mode="json"), "version_number": row.version_number}
            for row in artifacts
        ],
    }


async def selected_tabs(session, owner, connection_id, file_id, access):
    pair = (
        await session.execute(
            select(GoogleConnection, GoogleFileGrant)
            .join(GoogleFileGrant, GoogleFileGrant.connection_id == GoogleConnection.id)
            .where(
                GoogleConnection.id == connection_id,
                GoogleConnection.owner_id == owner,
                GoogleFileGrant.owner_id == owner,
                GoogleFileGrant.file_id == file_id,
                GoogleConnection.status == "connected",
                GoogleFileGrant.revoked.is_(False),
                GoogleConnection.generation == GoogleFileGrant.connection_generation,
                GoogleFileGrant.mime_type == SHEET_MIME,
            )
        )
    ).first()
    if pair is None:
        raise HTTPException(404, "Selected native Google Sheet not found")
    connection_generation, grant_generation = pair[0].generation, pair[1].generation
    token = await access.credentials.access_token(session, owner, connection_id)
    data = await access.credentials.http.request(
        "GET",
        SHEETS_URL + "/" + quote(file_id, safe=""),
        token=token,
        params={"fields": "sheets(properties(sheetId,title))"},
    )
    tabs = data.get("sheets")
    if not isinstance(tabs, list) or not tabs:
        raise GoogleServiceError(502, "Invalid Sheet tab metadata")
    result = []
    for tab in tabs:
        properties = tab.get("properties", {})
        if type(properties.get("sheetId")) is not int or not isinstance(
            properties.get("title"), str
        ):
            raise GoogleServiceError(502, "Invalid Sheet tab metadata")
        result.append({"sheet_id": properties["sheetId"], "title": properties["title"]})
    fresh = (
        await session.execute(
            select(GoogleConnection.generation, GoogleFileGrant.generation)
            .join(GoogleFileGrant, GoogleFileGrant.connection_id == GoogleConnection.id)
            .where(
                GoogleConnection.id == connection_id,
                GoogleConnection.owner_id == owner,
                GoogleConnection.status == "connected",
                GoogleFileGrant.file_id == file_id,
                GoogleFileGrant.owner_id == owner,
                GoogleFileGrant.revoked.is_(False),
                GoogleFileGrant.connection_generation == GoogleConnection.generation,
            )
        )
    ).first()
    if fresh is None or tuple(fresh) != (connection_generation, grant_generation):
        raise HTTPException(409, "Google selection changed; choose the file again")
    return {"items": result}
