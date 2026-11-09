"""Host-only bridge between networkless Python files and selected Google resources."""

import json
from dataclasses import replace
from pathlib import PurePosixPath

from sqlalchemy import select

from backend.models import AgentOperation, AgentRun, GoogleMutation
from backend.schemas.google import DOC_MIME, SHEET_MIME, GoogleFileRef, GoogleWritePreview
from backend.services.artifacts.storage import MIME_TYPES, StoredArtifact
from backend.services.google.access import google_url
from backend.services.google.client import DOCS_URL, SHEETS_URL
from backend.services.google.errors import GoogleServiceError
from backend.services.google.mutations import GoogleMutations, fingerprint
from backend.services.google.requests import validate_requests
from backend.services.google.snapshots import FILE_LIMIT, JSON_LIMIT, GoogleSnapshots, conflict
from backend.services.run_events import fence


class GoogleBridgeError(GoogleServiceError):
    def __init__(self, error, completed):
        super().__init__(error.status_code, error.detail, error.code)
        self.completed = completed


class GooglePythonBridge:
    def __init__(self, access, sessions, storage, *, context_service=None):
        self.access, self.sessions, self.storage = access, sessions, storage
        self.snapshots = GoogleSnapshots(access, sessions, storage)
        self.journal = GoogleMutations(access, sessions)
        self.context_service = context_service

    def scope(self):
        if self.context_service is None:
            from backend.services.google.tools import GoogleToolService

            self.context_service = GoogleToolService(access=self.access, sessions=self.sessions)
        return self.context_service

    async def check(self, context):
        async with self.sessions() as session:
            await fence(session)
            await self.scope().check_run(session, context)
            marker = await session.scalar(
                select(AgentOperation.id).where(
                    AgentOperation.agent_run_id == context.run_id,
                    AgentOperation.key == "google.conflict",
                )
            )
            if marker:
                raise conflict()
        await self.scope().validate_authority(context)

    async def mark_conflict(self, context):
        async with self.sessions() as session:
            await fence(session)
            await session.scalar(
                select(AgentRun.id).where(AgentRun.id == context.run_id).with_for_update()
            )
            marker = await session.scalar(
                select(AgentOperation).where(
                    AgentOperation.agent_run_id == context.run_id,
                    AgentOperation.key == "google.conflict",
                )
            )
            if marker is None:
                session.add(
                    AgentOperation(
                        agent_run_id=context.run_id,
                        key="google.conflict",
                        fingerprint="google-conflict-1",
                        status="completed",
                        result={"code": "google_conflict"},
                    )
                )
            await session.commit()

    async def authority(self, context, *, ref=None, connection_id=None, action, source=None):
        """Do not broaden saved ranged grants into whole-file native editing."""
        from backend.services.automations.authority import covers, resolve_run_authority

        async with self.sessions() as session:
            await fence(session)
            await self.scope().check_run(session, context)
            if ref is not None:
                await self.scope().require_ref(session, context, ref)
                connection_id = ref.connection_id
            authority = await resolve_run_authority(session, agent_run_id=context.run_id)
            if authority.owner != context.user_id:
                raise GoogleServiceError(403, "Google owner scope changed")
            if authority.source != "automation":
                return
            for grant in authority.grants:
                if grant.connection_id != connection_id:
                    continue
                if grant.file_id == (ref.file_id if ref else None):
                    if (
                        grant.action == action
                        or (action == "read_doc" and grant.action == "edit_doc")
                        or (action == "read_sheet" and grant.action == "edit_sheet")
                    ):
                        if action == "read_sheet" and grant.action == "read_sheet":
                            if (
                                source is None
                                or source.mode != "native"
                                or not source.range_a1
                                or not covers(grant, source.range_a1)
                            ):
                                continue
                        return
                if ref and grant.file_id is None and grant.action == "upload_artifact":
                    created = await session.scalars(
                        select(GoogleMutation).where(
                            GoogleMutation.run_id == context.run_id,
                            GoogleMutation.owner_id == context.user_id,
                            GoogleMutation.connection_id == connection_id,
                            GoogleMutation.kind == "upload_artifact",
                            GoogleMutation.status == "completed",
                        )
                    )
                    if any(
                        (row.receipt or {}).get("file_ref", {}).get("file_id") == ref.file_id
                        for row in created
                    ):
                        return
            raise GoogleServiceError(403, "Automation does not grant this Google file action")

    async def capture(self, context, source):
        await self.check(context)
        ref = GoogleFileRef.model_validate(source.ref.model_dump())
        async with self.sessions() as session:
            auth = await self.access.authorize(session, context, ref, "read_file")
        action = (
            "read_sheet"
            if auth.mime_type == SHEET_MIME
            else "read_doc"
            if auth.mime_type == DOC_MIME
            else "read_file"
        )
        await self.authority(context, ref=ref, action=action, source=source)
        try:
            return await self.snapshots.capture(context, source)
        except GoogleServiceError as error:
            if error.code == "google_conflict":
                await self.mark_conflict(context)
            raise

    async def validate_actions(self, context, request, snapshots, files):
        if len(files) != len(request.outputs):
            raise ValueError("Python output bundle does not match Google declarations")
        by_path = {
            declaration.path: (declaration, StoredArtifact(**metadata))
            for declaration, metadata in zip(request.outputs, files, strict=True)
        }
        plans = []
        for action in request.google_actions:
            snapshot = snapshots[action.input_index] if action.input_index is not None else None
            if action.kind == "batch_update":
                declaration, stored = by_path[action.request_path]
                self.storage.verify(stored, max_bytes=JSON_LIMIT)
                if stored.mime_type != "application/json" or declaration.publish:
                    raise ValueError("Google control output must be a private JSON file")
                try:
                    body = json.loads(self.storage.path(stored.key).read_bytes())
                except (ValueError, UnicodeError):
                    raise ValueError("Invalid Google control JSON") from None
                body = validate_requests(snapshot["mime_type"], body)
                plans.append(
                    {"action": action.model_dump(mode="json"), "snapshot": snapshot, "body": body}
                )
            else:
                declaration, stored = by_path[action.output_path]
                self.storage.verify(stored, max_bytes=FILE_LIMIT)
                if stored.mime_type not in {
                    MIME_TYPES["xlsx"],
                    MIME_TYPES["docx"],
                    MIME_TYPES["pdf"],
                }:
                    raise ValueError("Google upload supports XLSX, DOCX and PDF")
                conversion = {MIME_TYPES["xlsx"]: SHEET_MIME, MIME_TYPES["docx"]: DOC_MIME}
                if action.mode == "google_native" and stored.mime_type not in conversion:
                    raise ValueError("Only XLSX/DOCX can convert to native Google documents")
                if snapshot and stored.mime_type != snapshot["mime_type"]:
                    raise ValueError("Binary replacement must keep the original MIME type")
                name = declaration.name or PurePosixPath(declaration.path).name
                plans.append(
                    {
                        "action": action.model_dump(mode="json"),
                        "snapshot": snapshot,
                        "stored": vars(stored),
                        "metadata": {
                            "name": PurePosixPath(name).stem
                            if action.mode == "google_native"
                            else name,
                            "mimeType": conversion[stored.mime_type]
                            if action.mode == "google_native"
                            else stored.mime_type,
                        },
                    }
                )
        return plans

    async def execute(self, context, request, snapshots, files, published, job_key):
        await self.check(context)
        plans = await self.validate_actions(context, request, snapshots, files)
        completed, pending = [], []
        try:
            # Read completed receipts before source validation: our own previous writes
            # have legitimately changed those sources on crash recovery.
            for index, plan in enumerate(plans):
                operation = replace(context, operation_key=f"{job_key}:google:{index}")
                kind = (
                    "python_native_update"
                    if plan["action"]["kind"] == "batch_update"
                    else ("python_binary_update" if plan["snapshot"] else "upload_artifact")
                )
                key = fingerprint(
                    [str(context.run_id), context.task_id, operation.operation_key, kind]
                )
                async with self.sessions() as session:
                    row = await session.scalar(
                        select(GoogleMutation).where(
                            GoogleMutation.owner_id == context.user_id,
                            GoogleMutation.operation_key == key,
                        )
                    )
                    if row and row.status == "completed":
                        completed.append({"action_index": index, **row.receipt})
                        continue
                pending.append((index, plan, operation, kind))
            completed_targets = {
                plans[item["action_index"]]["action"].get("input_index") for item in completed
            }
            for index, snapshot in enumerate(snapshots):
                if index not in completed_targets:
                    await self.snapshots.validate(context, snapshot)
            # Validate every action's permission before the first mutation.
            for _, plan, _, _ in pending:
                await self.plan_authority(context, plan)
            for index, plan, operation, kind in pending:
                await self.check(context)
                receipt = await self.execute_one(operation, plan, kind)
                completed.append({"action_index": index, **receipt.model_dump(mode="json")})
            return sorted(completed, key=lambda item: item["action_index"])
        except GoogleServiceError as error:
            if error.code == "google_conflict":
                await self.mark_conflict(context)
            raise GoogleBridgeError(error, completed) from None

    async def plan_authority(self, context, plan):
        source = plan["snapshot"]
        if source:
            ref = GoogleFileRef.model_validate(source["source"]["ref"])
            mime = source["mime_type"]
            action = (
                "edit_sheet"
                if mime == SHEET_MIME
                else "edit_doc"
                if mime == DOC_MIME
                else "edit_file"
            )
            await self.authority(context, ref=ref, action=action)
            async with self.sessions() as session:
                return await self.access.authorize(
                    session,
                    context,
                    ref,
                    "write_sheet"
                    if mime == SHEET_MIME
                    else "write_doc"
                    if mime == DOC_MIME
                    else "write_file",
                )
        connection_id = request_connection(plan)
        await self.authority(context, connection_id=connection_id, action="upload_artifact")
        async with self.sessions() as session:
            return await self.access.authorize_connection(session, context, connection_id)

    async def execute_one(self, context, plan, kind):
        auth = await self.plan_authority(context, plan)
        source = plan["snapshot"]
        ref = auth.ref if source else None
        preview = GoogleWritePreview(
            operation=kind,
            title="Python Google output",
            target=ref.file_id if ref else "Google Drive",
            before="Pinned source" if source else "New file",
            after="Content/layout from Python",
            warning="",
        )
        digest = fingerprint(plan)
        proposal = await self.journal.prepare(
            context,
            connection_id=ref.connection_id if ref else auth.connection_id,
            file_id=ref.file_id if ref else None,
            connection_generation=auth.connection_generation,
            grant_generation=auth.grant_generation if source else None,
            kind=kind,
            request=plan,
            data={},
            snapshot_hash=digest,
            preview=preview,
        )

        async def authorize(session, row):
            await fence(session)
            return await self.plan_authority(context, plan)

        async def snapshot(row, current):
            if source:
                await self.snapshots.validate(context, source)
            return digest

        async def write(row, current, session):
            if source and plan["action"]["kind"] == "batch_update":
                body = plan["body"]
                if source["mime_type"] == DOC_MIME:
                    body = {**body, "writeControl": {"requiredRevisionId": source["revision_id"]}}
                endpoint = SHEETS_URL if source["mime_type"] == SHEET_MIME else DOCS_URL
                response = await self.access.credentials.http.request(
                    "POST",
                    endpoint + "/" + ref.file_id + ":batchUpdate",
                    token=current.token,
                    json=body,
                )
                identity_key = (
                    "spreadsheetId" if source["mime_type"] == SHEET_MIME else "documentId"
                )
                if response.get(identity_key) != ref.file_id:
                    raise GoogleServiceError(502, "Google mutation response identity unavailable")
                return {"url": google_url(ref.file_id, source["mime_type"])}
            stored = StoredArtifact(**plan["stored"])
            self.storage.verify(stored, max_bytes=FILE_LIMIT)
            if source:
                response = await self.access.credentials.http.upload_bytes(
                    token=current.token,
                    metadata=plan["metadata"],
                    content=self.storage.path(stored.key).read_bytes(),
                    source_mime=stored.mime_type,
                    file_id=ref.file_id,
                )
                if (
                    response.get("id") != ref.file_id
                    or response.get("mimeType") != stored.mime_type
                ):
                    raise GoogleServiceError(502, "Google binary update identity unavailable")
                return {"url": google_url(ref.file_id, source["mime_type"])}
            result = await self.access.credentials.http.upload_bytes(
                token=current.token,
                metadata=plan["metadata"],
                content=self.storage.path(stored.key).read_bytes(),
                source_mime=stored.mime_type,
            )
            identity = GoogleFileRef(
                connection_id=current.connection_id,
                context_id=context.conversation_id,
                file_id=result.get("id"),
            ).file_id
            metadata = await self.access.metadata(identity, current.token)
            if metadata["mimeType"] != plan["metadata"]["mimeType"]:
                raise GoogleServiceError(
                    502, "Uploaded Google file type differs from requested mode"
                )
            created = await self.access.register_created(session, context, current, metadata)
            return {
                "url": google_url(identity, metadata["mimeType"]),
                "file_ref": created.model_dump(mode="json"),
            }

        return await self.journal.execute(
            context,
            proposal_id=proposal.id,
            digest=proposal.digest,
            preview=proposal.preview,
            tool_name="run_python_code",
            authorize=authorize,
            snapshot=snapshot,
            write=write,
            automatic=True,
        )


def request_connection(plan):
    from uuid import UUID

    return UUID(plan["action"]["connection_id"])
