"""Chat/worker bridge: scoped input streams -> isolated runner -> immutable publication."""

import asyncio
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from agent_core.tools import ToolContext, ToolResult
from artifact_runtime.protocol import (
    DOCX_MIME,
    PDF_MIME,
    ArtifactInput,
    ArtifactOperation,
    ArtifactRunRequest,
    canonical,
)
from backend.database import SessionFactory
from backend.models import AgentRun, Conversation
from backend.models.artifact import ArtifactVersion, MessageArtifact
from backend.services.artifacts.service import editable_source, get_artifact_service, published
from backend.services.artifacts.storage import XLSX_MIME, ArtifactStorageError
from backend.services.artifacts.validation import (
    ValidatedArtifact,
    ValidatedCompanion,
    validate_archive,
    validate_name,
)


class ArtifactToolService:
    def __init__(self, *, service=None, sessions=None, runner_factory=None):
        self.service = service
        self.sessions = sessions or SessionFactory
        self.runner_factory = runner_factory

    def storage_service(self):
        return self.service or get_artifact_service()

    def runner(self, owner):
        if self.runner_factory:
            return self.runner_factory(owner)
        from backend.routes.artifacts import runner_client

        return runner_client(self.storage_service(), owner)

    async def check(self, context, tool):
        if context.authorization_guard:
            await context.authorization_guard(tool)
        async with self.sessions() as session:
            owner = await session.scalar(
                select(Conversation.id).where(
                    Conversation.id == context.conversation_id,
                    Conversation.user_id == context.user_id,
                )
            )
            if owner is None:
                raise HTTPException(404, "Conversation not found.")
            if context.run_id:
                run = await session.scalar(
                    select(AgentRun).where(
                        AgentRun.id == context.run_id,
                        AgentRun.conversation_id == context.conversation_id,
                    )
                )
                if run is None or run.status in {"cancelled", "failed"}:
                    raise asyncio.CancelledError()

    async def input_version(self, session, context, version_id):
        row = await self.storage_service().open_owned_version(session, context.user_id, version_id)
        if row.id not in context.artifact_version_ids:
            # Generated versions remain usable in follow-ups; explicit input selection stays pinned.
            conversation = await session.scalar(
                select(AgentRun.conversation_id).where(
                    AgentRun.id == row.source_run_id,
                    AgentRun.conversation_id == context.conversation_id,
                )
            )
            if conversation is None:
                binding = await session.scalar(
                    select(MessageArtifact.message_id)
                    .where(
                        MessageArtifact.version_id == row.id,
                        MessageArtifact.owner_user_id == context.user_id,
                        MessageArtifact.conversation_id == context.conversation_id,
                    )
                    .limit(1)
                )
                if binding is None:
                    raise HTTPException(404, "Artifact is not in this run's scope.")
        if row.mime_type not in {XLSX_MIME, DOCX_MIME, PDF_MIME}:
            raise HTTPException(415, "This document format is not supported yet.")
        return row

    async def run(
        self,
        context,
        job,
        *,
        stored=None,
        additional=(),
        operation=None,
        action="execute",
        **kwargs,
    ):
        service = self.storage_service()
        with ExitStack() as stack:
            inputs = []
            for index, item in enumerate(([stored] if stored else []) + list(additional)):
                source = stack.enter_context(service.storage.path(item.key).open("rb"))
                inputs.append(
                    ArtifactInput(
                        f"input-{index}" + service.storage.path(item.key).suffix,
                        item.mime_type,
                        item.size_bytes,
                        item.sha256,
                        source,
                    )
                )
            return await self.runner(context.user_id).execute(
                ArtifactRunRequest(
                    job_id=job,
                    operation=operation,
                    action=action,
                    inputs=tuple(item.descriptor() for item in inputs),
                    **kwargs,
                ),
                tuple(inputs),
                cancel=lambda: self.check(
                    context, "artifact_inspect" if action == "inspect" else "artifact_edit"
                ),
            )

    async def inspect(self, context, *, version_id, sheet=None, offset=0, limit=20, text_offset=0):
        from uuid import uuid4

        from backend.services.artifacts.storage import StoredArtifact

        try:
            await self.check(context, "artifact_inspect")
            async with self.sessions() as session:
                row = await self.input_version(session, context, version_id)
                stored = StoredArtifact(row.storage_key, row.size_bytes, row.sha256, row.mime_type)
                if row.mime_type == PDF_MIME and (source := editable_source(row)):
                    await asyncio.to_thread(
                        self.storage_service().storage.verify,
                        source,
                        max_bytes=self.storage_service().max_file_bytes,
                    )
                    stored = source
                ref = published(row)
            result = await self.run(
                context,
                uuid4(),
                stored=stored,
                action="inspect",
                preview_sheet=sheet,
                preview_offset=offset,
                preview_limit=limit,
                preview_text_offset=text_offset,
            )
            await self.check(context, "artifact_inspect")
            preview = json.dumps(result.manifest.preview_manifest, ensure_ascii=False)
            return ToolResult.success(
                preview[:16000], data={"artifact_refs": [ref.model_dump(mode="json")]}
            )
        except (HTTPException, ValueError, ArtifactStorageError, httpx.HTTPError) as error:
            return self.failure(error)

    @staticmethod
    def failure(error):
        if isinstance(error, HTTPException) and error.status_code == 404:
            return ToolResult.failure(
                "File không nằm trong phạm vi đã chọn của hội thoại.",
                error_code="artifact_not_in_scope",
            )
        if isinstance(error, httpx.HTTPError):
            return ToolResult.failure(
                "Artifact runner chưa sẵn sàng; chưa tạo phiên bản mới.",
                error_code="tool_unavailable",
            )
        detail = (
            error.detail if isinstance(error, (HTTPException, ArtifactStorageError)) else str(error)
        )
        return ToolResult.failure(
            str(detail)[-1000:] or "Không thể xử lý file.", error_code="artifact_validation_failed"
        )

    async def execute(
        self, context: ToolContext, request: ArtifactOperation, *, name=None
    ) -> ToolResult:
        service = self.storage_service()
        cleanup = set()
        tool = {
            "python": "run_artifact_python",
            "create_xlsx": "artifact_create",
            "edit_xlsx": "artifact_edit",
            "create_docx": "artifact_create",
            "edit_docx": "artifact_edit",
            "rebuild_pdf": "artifact_edit" if request.parent_version_id else "artifact_create",
            "rebuild_pdf_text_only": "artifact_rebuild_pdf_text_only",
        }[request.kind]
        try:
            await self.check(context, tool)
            if context.run_id is None or not context.operation_key:
                raise ValueError("File actions require a durable run and operation identity.")
            op = hashlib.sha256(f"{context.run_id}:{context.operation_key}".encode()).hexdigest()
            key = "artifact:" + op
            fingerprint = hashlib.sha256(
                canonical({"operation": request.model_dump(mode="json"), "name": name})
            ).hexdigest()
            async with self.sessions() as session:
                parent = (
                    await self.input_version(session, context, request.parent_version_id)
                    if request.parent_version_id
                    else None
                )
                previous = await session.scalar(
                    select(ArtifactVersion).where(
                        ArtifactVersion.owner_user_id == context.user_id,
                        ArtifactVersion.operation_key == key,
                    )
                )
                if previous:
                    if {
                        "role": "operation_fingerprint",
                        "sha256": fingerprint,
                    } not in previous.companion_sources:
                        raise ValueError("Operation input changed; start a new operation.")
                    return ToolResult.success(
                        "Phiên bản đã được lưu.",
                        data={"artifact_refs": [published(previous).model_dump(mode="json")]},
                    )
                extension = (
                    ".pdf"
                    if request.kind.startswith("rebuild_pdf")
                    else ".docx"
                    if request.kind.endswith("docx")
                    else ".xlsx"
                )
                target = validate_name(name or (parent.name if parent else "report" + extension))
                if Path(target).suffix.lower() not in {".xlsx", ".docx", ".pdf"} or (
                    request.kind != "python" and Path(target).suffix.lower() != extension
                ):
                    raise ValueError("Output filename must match the operation format.")
                if parent and Path(parent.name).suffix.lower() != Path(target).suffix.lower():
                    raise ValueError("Input format does not match the edit operation.")
                from backend.services.artifacts.storage import StoredArtifact

                input = (
                    StoredArtifact(
                        parent.storage_key, parent.size_bytes, parent.sha256, parent.mime_type
                    )
                    if parent
                    else None
                )
                source = (
                    editable_source(parent) if parent and parent.mime_type == PDF_MIME else None
                )
                if source:
                    await asyncio.to_thread(
                        service.storage.verify, source, max_bytes=service.max_file_bytes
                    )

            def job(stage):
                return uuid5(
                    NAMESPACE_URL, f"artifact:{context.user_id}:{op}:{fingerprint}:{stage}"
                )

            generated = await self.run(
                context,
                job("execute"),
                stored=input,
                additional=(source,) if source else (),
                operation=request,
            )
            cleanup.update(item.key for item in generated.files)
            pdf = Path(target).suffix.lower() == ".pdf"
            companions = ()
            if len(generated.files) != (2 if pdf else 1):
                raise ValueError("Expected primary output and, for PDF, an editable DOCX source.")
            current = generated.files[0]
            if (
                current.mime_type
                != {".xlsx": XLSX_MIME, ".docx": DOCX_MIME, ".pdf": PDF_MIME}[
                    Path(target).suffix.lower()
                ]
            ):
                raise ValueError("Generated file format does not match the requested output.")
            if not pdf:
                await asyncio.to_thread(validate_archive, service.storage.path(current.key))
            inspection = await self.run(context, job("preflight"), stored=current, action="inspect")
            calculation = inspection.manifest.calculation
            if pdf:
                editable = generated.files[1]
                if (
                    editable.mime_type != DOCX_MIME
                    or generated.manifest.preview_manifest.get("editable_source") != "output-1.docx"
                ):
                    raise ValueError("PDF editable source is missing or invalid")
                await asyncio.to_thread(validate_archive, service.storage.path(editable.key))
                await self.run(context, job("source-preflight"), stored=editable, action="inspect")
                companions = (ValidatedCompanion("editable_source", editable),)
            if current.mime_type == XLSX_MIME and calculation.status != "unsupported":
                recalculated = await self.run(
                    context, job("calculate"), stored=current, action="recalculate"
                )
                cleanup.update(item.key for item in recalculated.files)
                if (
                    recalculated.manifest.calculation.status != "fresh"
                    or len(recalculated.files) != 1
                ):
                    raise ValueError("Recalculation did not produce a validated workbook.")
                current = recalculated.files[0]
                calculation = recalculated.manifest.calculation
                inspection = await self.run(
                    context, job("preview"), stored=current, action="inspect"
                )
            await self.check(context, tool)
            staged = ValidatedArtifact(
                current,
                target,
                calculation_status=calculation.status,
                preview_manifest=inspection.manifest.preview_manifest,
                companion_sources=[{"role": "operation_fingerprint", "sha256": fingerprint}],
                source_run_id=context.run_id,
                companions=companions,
            )
            async with self.sessions() as session:
                from backend.services.run_events import fence

                await fence(session)
                # Hand off cleanup only when publication actually starts. A failed lease
                # fence still belongs to this scope, whereas an ambiguous COMMIT does not.
                cleanup.discard(current.key)
                cleanup.difference_update(item.stored.key for item in companions)
                ref = await service.publish_version(
                    session,
                    context.user_id,
                    artifact_id=parent.artifact_id if parent else None,
                    parent_version_id=parent.id if parent else None,
                    staged=staged,
                    operation_key=key,
                )
            return ToolResult.success(
                "Đã lưu phiên bản file mới; bản gốc được giữ nguyên.",
                data={
                    "artifact_refs": [ref.model_dump(mode="json")],
                    "warnings": list(calculation.warnings),
                },
            )
        except (HTTPException, ValueError, ArtifactStorageError, httpx.HTTPError) as error:
            return self.failure(error)
        finally:
            for key in cleanup:
                await asyncio.to_thread(service.storage.delete, key)
