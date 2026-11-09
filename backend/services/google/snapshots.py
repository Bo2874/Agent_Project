"""Immutable, credential-free inputs captured under current per-file authority."""

import hashlib
import io
import json
from dataclasses import asdict
from uuid import uuid4

from artifact_runtime.google_types import PythonGoogleInput
from backend.schemas.google import DOC_MIME, SHEET_MIME, GoogleFileRef
from backend.services.artifacts.storage import MIME_TYPES
from backend.services.google.client import DOCS_URL, DRIVE_URL, SHEETS_URL
from backend.services.google.errors import GoogleServiceError
from backend.services.google.ranges import parse_range

JSON_LIMIT = 2097152
FILE_LIMIT = 20971520


def conflict():
    return GoogleServiceError(
        409, "Google source changed; stop and start a new request", "google_conflict"
    )


def encoded(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class GoogleSnapshots:
    def __init__(self, access, sessions, storage):
        self.access, self.sessions, self.storage = access, sessions, storage

    async def observe(self, context, source):
        ref = GoogleFileRef.model_validate(source.ref.model_dump())
        async with self.sessions() as session:
            auth = await self.access.authorize(session, context, ref, "read_file")
        before = await self.access.metadata(ref.file_id, auth.token)
        version = before.get("version")
        if not isinstance(version, str) or not version.isdecimal():
            raise GoogleServiceError(502, "Google source has no usable version")
        mime = auth.mime_type
        http = self.access.credentials.http
        revision = None
        if source.mode == "native":
            if mime == SHEET_MIME:
                bounds = source.range_a1 or "A1:AX1000"
                parse_range(bounds)
                body = await http.request(
                    "GET",
                    SHEETS_URL + "/" + ref.file_id,
                    token=auth.token,
                    params={"ranges": bounds, "includeGridData": "true"},
                )
                if body.get("spreadsheetId") != ref.file_id or not isinstance(
                    body.get("sheets"), list
                ):
                    raise GoogleServiceError(502, "Invalid native Sheets snapshot")
            elif mime == DOC_MIME:
                if source.range_a1 is not None:
                    raise GoogleServiceError(422, "Only Sheets accepts a range")
                body = await http.request(
                    "GET",
                    DOCS_URL + "/" + ref.file_id,
                    token=auth.token,
                    params={"includeTabsContent": "true"},
                )
                revision = body.get("revisionId")
                if (
                    body.get("documentId") != ref.file_id
                    or not isinstance(revision, str)
                    or not revision
                ):
                    raise GoogleServiceError(502, "Invalid native Docs snapshot")
            else:
                raise GoogleServiceError(422, "Native mode requires Google Sheets or Docs")
            content = encoded(body)
            if len(content) > JSON_LIMIT:
                raise GoogleServiceError(413, "Google snapshot exceeds 2 MiB; read a smaller range")
            stored_mime = "application/json"
        else:
            if source.mode == "download":
                if mime in {SHEET_MIME, DOC_MIME}:
                    raise GoogleServiceError(
                        422, "Native Google files require export or native mode"
                    )
                endpoint, params, stored_mime = (
                    DRIVE_URL + "/files/" + ref.file_id,
                    {"alt": "media"},
                    mime,
                )
            else:
                supported = {
                    SHEET_MIME: {MIME_TYPES["xlsx"], MIME_TYPES["pdf"]},
                    DOC_MIME: {MIME_TYPES["docx"], MIME_TYPES["pdf"]},
                }
                if source.export_mime not in supported.get(mime, set()):
                    raise GoogleServiceError(422, "Unsupported Google export format")
                endpoint = DRIVE_URL + "/files/" + ref.file_id + "/export"
                params, stored_mime = {"mimeType": source.export_mime}, source.export_mime
            content = await http.download_bytes(
                endpoint, token=auth.token, max_bytes=FILE_LIMIT, params=params
            )
        after = await self.access.metadata(ref.file_id, auth.token)
        if after.get("version") != version or after.get("mimeType") != mime:
            raise conflict()
        # Recheck revocation and generations after network operations.
        async with self.sessions() as session:
            current = await self.access.authorize(session, context, ref, "read_file")
        if (current.connection_generation, current.grant_generation) != (
            auth.connection_generation,
            auth.grant_generation,
        ):
            raise conflict()
        return (
            content,
            {
                "source": source.model_dump(mode="json"),
                "mime_type": mime,
                "version": version,
                "revision_id": revision,
                "sha256": hashlib.sha256(content).hexdigest(),
                "connection_generation": auth.connection_generation,
                "grant_generation": auth.grant_generation,
            },
            stored_mime,
        )

    async def capture(self, context, source):
        content, snapshot, mime = await self.observe(context, source)
        extension = next((key for key, value in MIME_TYPES.items() if value == mime), None)
        if extension is None:
            raise GoogleServiceError(422, "Unsupported Google input format")
        key = f"{context.user_id}/{uuid4()}/{uuid4()}/content.{extension}"
        stored = self.storage.put(io.BytesIO(content), key, max_bytes=FILE_LIMIT)
        return {**snapshot, "stored": asdict(stored)}

    async def validate(self, context, snapshot):
        source = PythonGoogleInput.model_validate(snapshot["source"])
        _, current, _ = await self.observe(context, source)
        if any(
            current[key] != snapshot[key]
            for key in (
                "version",
                "revision_id",
                "sha256",
                "connection_generation",
                "grant_generation",
                "mime_type",
            )
        ):
            raise conflict()
