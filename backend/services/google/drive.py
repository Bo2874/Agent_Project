"""Export only owned, scoped immutable ArtifactVersion bytes; no model paths or URLs."""

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import PurePath

from fastapi import HTTPException

from backend.schemas.google import DOC_MIME, SHEET_MIME, GoogleFileRef, GoogleWritePreview
from backend.services.artifacts.storage import MIME_TYPES, ArtifactStorageError
from backend.services.artifacts.tools import ArtifactToolService
from backend.services.google.access import google_url
from backend.services.google.errors import GoogleServiceError
from backend.services.google.mutations import GoogleMutations, fingerprint
from backend.services.google.sheets import snippet


@dataclass(frozen=True)
class UploadAuthority:
    connection_id: object
    connection_generation: int
    descriptor: dict
    token: str = field(repr=False)
    content: bytes = field(repr=False)


class GoogleDrive:
    def __init__(self, access, sessions, *, artifacts=None):
        self.access, self.sessions = access, sessions
        self.artifacts = artifacts or ArtifactToolService(sessions=sessions)
        self.journal = GoogleMutations(access, sessions)

    async def input(self, session, context, version_id):
        try:
            version = await self.artifacts.input_version(session, context, version_id)
            descriptor = {
                "version_id": str(version.id),
                "name": version.name,
                "mime_type": version.mime_type,
                "size_bytes": version.size_bytes,
                "sha256": version.sha256,
            }
            if not 1 <= version.size_bytes <= 20971520:
                raise GoogleServiceError(413, "Google upload exceeds the 20 MiB limit")
            storage = self.artifacts.storage_service().storage
            key = version.storage_key

            def read():
                with storage.path(key).open("rb") as stream:
                    content = stream.read(20971521)
                if (
                    len(content) != descriptor["size_bytes"]
                    or hashlib.sha256(content).hexdigest() != descriptor["sha256"]
                ):
                    raise GoogleServiceError(
                        422, "Artifact bytes no longer match the pinned version"
                    )
                return content

            return descriptor, await asyncio.to_thread(read)
        except HTTPException:
            raise GoogleServiceError(
                404, "Artifact version is not owned or selected for this run"
            ) from None
        except (ArtifactStorageError, OSError):
            raise GoogleServiceError(422, "Artifact bytes unavailable") from None

    async def prepare(self, context, connection_id, version_id, *, mode):
        if mode not in {"keep_format", "google_native"}:
            raise GoogleServiceError(422, "Choose explicit keep_format or google_native")
        async with self.sessions() as session:
            descriptor, _ = await self.input(session, context, version_id)
            auth = await self.access.authorize_connection(session, context, connection_id)
        source_mime = descriptor["mime_type"]
        conversion = {MIME_TYPES["xlsx"]: SHEET_MIME, MIME_TYPES["docx"]: DOC_MIME}
        if mode == "google_native" and source_mime not in conversion:
            raise GoogleServiceError(422, "Only XLSX/DOCX support explicit native conversion")
        metadata = {
            "name": PurePath(descriptor["name"]).stem
            if mode == "google_native"
            else descriptor["name"],
            "mimeType": conversion[source_mime] if mode == "google_native" else source_mime,
        }
        data = {"version": descriptor, "mode": mode, "metadata": metadata}
        return await self.journal.prepare(
            context,
            connection_id=connection_id,
            file_id=None,
            connection_generation=auth.connection_generation,
            grant_generation=None,
            kind="upload_artifact",
            request={"version_id": str(version_id), "mode": mode},
            data=data,
            snapshot_hash=fingerprint(descriptor),
            preview=GoogleWritePreview(
                operation="upload_artifact",
                title="Upload: " + descriptor["name"],
                target="Google Drive (My Drive)",
                before=snippet(descriptor),
                after=snippet(data),
                warning="Conversion may cause loss of formatting, formulas, charts or layout. "
                "review the converted file. Original Artifact version is retained. "
                "Unknown upload outcome must be reconciled; never re-upload blindly."
                if mode == "google_native"
                else "Keeps the original binary format. No sharing/ownership/deletion changes. "
                "Unknown upload outcome must be reconciled; never re-upload blindly.",
            ),
        )

    async def execute(self, context, **arguments):
        from uuid import UUID

        async def authorize(session, row):
            if row.kind != "upload_artifact":
                raise GoogleServiceError(422, "Proposal is not an Artifact export")
            descriptor, content = await self.input(
                session, context, UUID(row.payload["request"]["version_id"])
            )
            auth = await self.access.authorize_connection(session, context, row.connection_id)
            return UploadAuthority(
                auth.connection_id, auth.connection_generation, descriptor, auth.token, content
            )

        async def snapshot(row, auth):
            return fingerprint(auth.descriptor)

        async def write(row, auth, session):
            result = await self.access.credentials.http.upload_bytes(
                token=auth.token,
                metadata=row.payload["data"]["metadata"],
                content=auth.content,
                source_mime=auth.descriptor["mime_type"],
            )
            identity = GoogleFileRef(
                connection_id=row.connection_id,
                file_id=result.get("id"),
                context_id=context.conversation_id,
            ).file_id
            metadata = await self.access.metadata(identity, auth.token)
            if metadata["mimeType"] != row.payload["data"]["metadata"]["mimeType"]:
                raise GoogleServiceError(
                    422, "Uploaded Google file type differs from approved mode"
                )
            ref = await self.access.register_created(session, context, auth, metadata)
            return {
                "url": google_url(identity, metadata["mimeType"]),
                "file_ref": ref.model_dump(mode="json"),
            }

        return await self.journal.execute(
            context,
            tool_name="google_drive_upload",
            authorize=authorize,
            snapshot=snapshot,
            write=write,
            **arguments,
        )
