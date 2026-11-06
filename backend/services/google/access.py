"""File grants, work-context selection and live authority checks for every operation."""

from dataclasses import dataclass, field
from urllib.parse import quote

from sqlalchemy import delete, select

from backend.models import Conversation, GoogleFileGrant, GoogleWorkFile
from backend.schemas.google import DOC_MIME, SHEET_MIME, GoogleFileRef
from backend.services.google.client import DRIVE_URL
from backend.services.google.errors import GoogleServiceError

SUPPORTED_MIMES = {
    SHEET_MIME,
    DOC_MIME,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/pdf",
}


def google_url(file_id, mime):
    segment = "spreadsheets" if mime == SHEET_MIME else "document" if mime == DOC_MIME else None
    return (
        f"https://docs.google.com/{segment}/d/{quote(file_id, safe='')}/edit"
        if segment
        else f"https://drive.google.com/file/d/{quote(file_id, safe='')}/view"
    )


def grant_view(row, context_id=None):
    result = {
        "connection_id": str(row.connection_id),
        "file_id": row.file_id,
        "name": row.name,
        "mime_type": row.mime_type,
        "can_edit": row.can_edit,
        "url": google_url(row.file_id, row.mime_type),
    }
    if context_id:
        result["context_id"] = str(context_id)
    return result


@dataclass(frozen=True)
class AuthorizedGoogleFile:
    ref: GoogleFileRef
    name: str
    mime_type: str
    grant_generation: int
    connection_generation: int
    token: str = field(repr=False)


@dataclass(frozen=True)
class AuthorizedGoogleConnection:
    connection_id: object
    connection_generation: int
    token: str = field(repr=False)


class GoogleAccess:
    def __init__(self, credentials):
        self.credentials = credentials

    async def authorize_connection(self, session, context, connection_id):
        await self.conversation(session, context.user_id, context.conversation_id)
        row = await self.credentials.owned(session, context.user_id, connection_id)
        generation = row.generation
        token = await self.credentials.access_token(session, context.user_id, connection_id)
        row = await self.credentials.owned(session, context.user_id, connection_id)
        if row.status != "connected" or row.generation != generation:
            raise GoogleServiceError(409, "Google connection changed; prepare again")
        return AuthorizedGoogleConnection(connection_id, generation, token)

    async def register_created(self, session, context, auth, metadata):
        ref = GoogleFileRef(
            connection_id=auth.connection_id,
            file_id=metadata["id"],
            context_id=context.conversation_id,
        )
        connection = await self.credentials.owned(
            session, context.user_id, auth.connection_id, lock=True
        )
        await self.conversation(session, context.user_id, context.conversation_id)
        if connection.status != "connected" or connection.generation != auth.connection_generation:
            raise GoogleServiceError(409, "Google connection changed during creation")
        grant = GoogleFileGrant(
            owner_id=context.user_id,
            connection_id=auth.connection_id,
            file_id=ref.file_id,
            name=metadata["name"],
            mime_type=metadata["mimeType"],
            can_edit=bool(metadata.get("capabilities", {}).get("canEdit")),
            generation=1,
            connection_generation=connection.generation,
            revoked=False,
        )
        session.add(grant)
        await session.flush()
        session.add(
            GoogleWorkFile(
                context_id=context.conversation_id,
                grant_id=grant.id,
                owner_id=context.user_id,
                connection_id=auth.connection_id,
                grant_generation=1,
            )
        )
        return ref

    async def conversation(self, session, owner, context_id):
        row = await session.scalar(
            select(Conversation).where(Conversation.id == context_id, Conversation.user_id == owner)
        )
        if row is None:
            raise GoogleServiceError(404, "Conversation not found")
        return row

    async def metadata(self, file_id, token):
        data = await self.credentials.http.request(
            "GET",
            DRIVE_URL + "/files/" + quote(file_id, safe=""),
            token=token,
            params={
                "fields": "id,name,mimeType,trashed,capabilities(canEdit)",
                "supportsAllDrives": "true",
            },
        )
        if (
            data.get("id") != file_id
            or data.get("trashed")
            or data.get("mimeType") not in SUPPORTED_MIMES
        ):
            raise GoogleServiceError(
                422, "Unsupported Google file type; select a document, not a folder"
            )
        if not isinstance(data.get("name"), str) or not 1 <= len(data["name"]) <= 512:
            raise GoogleServiceError(502, "Invalid Google file metadata")
        return data

    async def list_files(self, session, owner, connection_id):
        connection = await self.credentials.owned(session, owner, connection_id)
        rows = await session.scalars(
            select(GoogleFileGrant)
            .where(
                GoogleFileGrant.connection_id == connection_id,
                GoogleFileGrant.owner_id == owner,
                GoogleFileGrant.revoked.is_(False),
                GoogleFileGrant.connection_generation == connection.generation,
            )
            .order_by(GoogleFileGrant.name)
        )
        return [grant_view(row) for row in rows]

    async def set_files(self, session, owner, connection_id, file_ids):
        if len(file_ids) > 100 or len(set(file_ids)) != len(file_ids):
            raise GoogleServiceError(422, "Select at most 100 unique files")
        for file_id in file_ids:
            GoogleFileRef(connection_id=connection_id, file_id=file_id, context_id=owner)
        connection = await self.credentials.owned(session, owner, connection_id)
        generation = connection.generation
        token = await self.credentials.access_token(session, owner, connection_id)
        metadata = [await self.metadata(identity, token) for identity in file_ids]
        connection = await self.credentials.owned(session, owner, connection_id, lock=True)
        if connection.generation != generation or connection.status != "connected":
            raise GoogleServiceError(409, "Google connection changed; select files again")
        existing = list(
            await session.scalars(
                select(GoogleFileGrant).where(GoogleFileGrant.connection_id == connection_id)
            )
        )
        by_id = {row.file_id: row for row in existing}
        for row in existing:
            if row.file_id not in file_ids and not row.revoked:
                row.revoked = True
                row.generation += 1
        for item in metadata:
            row = by_id.get(item["id"])
            if row is None:
                row = GoogleFileGrant(
                    owner_id=owner, connection_id=connection_id, file_id=item["id"], generation=1
                )
                session.add(row)
            elif row.revoked or row.connection_generation != generation:
                row.generation += 1
            row.revoked, row.connection_generation = False, generation
            row.name, row.mime_type = item["name"], item["mimeType"]
            row.can_edit = bool(item.get("capabilities", {}).get("canEdit"))
        await session.commit()
        return await self.list_files(session, owner, connection_id)

    async def revoke_file(self, session, owner, connection_id, file_id):
        await self.credentials.owned(session, owner, connection_id, lock=True)
        row = await session.scalar(
            select(GoogleFileGrant).where(
                GoogleFileGrant.connection_id == connection_id,
                GoogleFileGrant.file_id == file_id,
                GoogleFileGrant.owner_id == owner,
            )
        )
        if row is None:
            raise GoogleServiceError(404, "Google file not found")
        row.revoked, row.generation = True, row.generation + 1
        await session.commit()

    async def set_work_files(self, session, owner, context_id, refs):
        await self.conversation(session, owner, context_id)
        if len(refs) > 20 or len({(ref.connection_id, ref.file_id) for ref in refs}) != len(refs):
            raise GoogleServiceError(422, "Select at most 20 unique work files")
        chosen = []
        for ref in refs:
            if ref.context_id != context_id:
                raise GoogleServiceError(404, "Google work context not found")
            connection = await self.credentials.owned(session, owner, ref.connection_id)
            row = await session.scalar(
                select(GoogleFileGrant)
                .where(
                    GoogleFileGrant.connection_id == ref.connection_id,
                    GoogleFileGrant.owner_id == owner,
                    GoogleFileGrant.file_id == ref.file_id,
                )
                .execution_options(populate_existing=True)
            )
            if (
                row is None
                or row.revoked
                or row.connection_generation != connection.generation
                or connection.status != "connected"
            ):
                raise GoogleServiceError(404, "Google file grant unavailable")
            chosen.append(row)
        await session.execute(
            delete(GoogleWorkFile).where(
                GoogleWorkFile.context_id == context_id, GoogleWorkFile.owner_id == owner
            )
        )
        session.add_all(
            [
                GoogleWorkFile(
                    context_id=context_id,
                    grant_id=row.id,
                    owner_id=owner,
                    connection_id=row.connection_id,
                    grant_generation=row.generation,
                )
                for row in chosen
            ]
        )
        await session.commit()
        return await self.selected(session, owner, context_id)

    async def selected(self, session, owner, context_id):
        await self.conversation(session, owner, context_id)
        rows = await session.execute(
            select(GoogleWorkFile, GoogleFileGrant)
            .join(GoogleFileGrant, GoogleWorkFile.grant_id == GoogleFileGrant.id)
            .where(GoogleWorkFile.owner_id == owner, GoogleWorkFile.context_id == context_id)
        )
        result = []
        for selection, grant in rows:
            connection = await self.credentials.owned(session, owner, grant.connection_id)
            if (
                not grant.revoked
                and grant.generation == selection.grant_generation
                and grant.connection_generation == connection.generation
                and connection.status == "connected"
            ):
                result.append(grant_view(grant, context_id))
        return result

    async def authorize(self, session, context, ref, action):
        if ref.context_id != context.conversation_id:
            raise GoogleServiceError(404, "Google work context not found")
        await self.conversation(session, context.user_id, ref.context_id)
        connection = await self.credentials.owned(session, context.user_id, ref.connection_id)
        generation = connection.generation
        pair = (
            await session.execute(
                select(GoogleWorkFile, GoogleFileGrant)
                .join(GoogleFileGrant, GoogleWorkFile.grant_id == GoogleFileGrant.id)
                .where(
                    GoogleWorkFile.owner_id == context.user_id,
                    GoogleWorkFile.context_id == ref.context_id,
                    GoogleFileGrant.connection_id == ref.connection_id,
                    GoogleFileGrant.file_id == ref.file_id,
                )
                .execution_options(populate_existing=True)
            )
        ).first()
        if (
            not pair
            or pair[1].revoked
            or pair[1].generation != pair[0].grant_generation
            or pair[1].connection_generation != generation
        ):
            raise GoogleServiceError(404, "Google file is not selected for this conversation")
        grant_generation = pair[1].generation
        token = await self.credentials.access_token(session, context.user_id, ref.connection_id)
        metadata = await self.metadata(ref.file_id, token)
        expected = {
            "read_sheet": SHEET_MIME,
            "write_sheet": SHEET_MIME,
            "read_doc": DOC_MIME,
            "write_doc": DOC_MIME,
            "read_file": "selected_metadata",
        }.get(action)
        if expected is None or (
            expected != "selected_metadata" and metadata["mimeType"] != expected
        ):
            raise GoogleServiceError(
                422,
                "Google file is not the required native type; "
                "Office files need explicit conversion",
            )
        if action.startswith("write_") and not metadata.get("capabilities", {}).get("canEdit"):
            raise GoogleServiceError(403, "Google file is read-only")
        # Recheck after network/refresh commits: revocation wins before the next call.
        connection = await self.credentials.owned(session, context.user_id, ref.connection_id)
        grant = await session.scalar(
            select(GoogleFileGrant)
            .where(
                GoogleFileGrant.connection_id == ref.connection_id,
                GoogleFileGrant.file_id == ref.file_id,
                GoogleFileGrant.owner_id == context.user_id,
            )
            .execution_options(populate_existing=True)
        )
        selection = (
            await session.get(GoogleWorkFile, (ref.context_id, grant.id), populate_existing=True)
            if grant
            else None
        )
        if (
            connection.status != "connected"
            or connection.generation != generation
            or grant is None
            or grant.revoked
            or grant.generation != grant_generation
            or selection is None
            or selection.grant_generation != grant_generation
        ):
            raise GoogleServiceError(409, "Google selection changed; select files again")
        return AuthorizedGoogleFile(
            ref, metadata["name"], metadata["mimeType"], grant_generation, generation, token
        )


async def authorize_google_file(session, context, ref, action):
    from backend.core.config import get_settings
    from backend.services.google.credentials import GoogleCredentials

    return await GoogleAccess(GoogleCredentials(get_settings())).authorize(
        session, context, ref, action
    )
