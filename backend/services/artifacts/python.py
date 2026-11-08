"""Scoped Python jobs, durable observations and immutable file publication.

The broker owns execution replay. This service owns file identities, receipts,
per-output publication keys and the final primary selection; no code runs here.
"""

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from agent_core.tools import ToolResult
from artifact_runtime.protocol import ArtifactOperation, canonical
from backend.models import AgentOperation, AgentRun
from backend.models.artifact import ArtifactVersion
from backend.services.artifacts.service import published
from backend.services.artifacts.storage import MIME_TYPES, ArtifactStorageError, StoredArtifact
from backend.services.artifacts.tools import ArtifactToolService
from backend.services.artifacts.validation import ValidatedArtifact, validate_name
from backend.services.run_events import fence

CONTRACT_VERSION = "unified-python-1"
PRIMARY_KEY = "python.primary"


def receipt_key(context):
    return "python.receipt." + hashlib.sha256(context.operation_key.encode()).hexdigest()


class PythonToolService(ArtifactToolService):
    input_mime_types = set(MIME_TYPES.values())

    def __init__(self, *, publication_enabled=None, **kwargs):
        super().__init__(**kwargs)
        self.publication_enabled = publication_enabled

    async def check(self, context, tool="run_python_code"):
        await super().check(context, "run_python_code")

    async def text_only_intent(self, context):
        from backend.models import Message

        async with self.sessions() as session:
            text = await session.scalar(
                select(Message.content)
                .join(AgentRun, AgentRun.user_message_id == Message.id)
                .where(
                    AgentRun.id == context.run_id,
                    Message.conversation_id == context.conversation_id,
                    Message.role == "user",
                )
            )
        # A content-loss clarification, not permission to run Python. The exact
        # phrase avoids treating quoted text/model assertions as user consent.
        return bool(
            text
            and text.strip().casefold().rstrip(".! ")
            in {"đồng ý chỉ xử lý văn bản", "i agree to text-only processing"}
        )

    async def allowed(self, context, request):
        await self.check(context)
        from backend.core.config import get_settings

        enabled = self.publication_enabled
        if enabled is None:
            enabled = get_settings().workspace_artifacts_enabled
        if not enabled and (request.input_version_ids or request.outputs):
            raise ValueError("File publication is disabled; Python calculations remain available.")
        async with self.sessions() as session:
            from backend.services.automations.authority import resolve_run_authority

            authority = await resolve_run_authority(session, agent_run_id=context.run_id)
            if authority.source == "automation":
                required = set()
                if request.input_version_ids:
                    required.add("inspect")
                for output in request.outputs:
                    required.add("edit" if output.parent_version_id else "create")
                if not required <= set(authority.revision.artifact_actions):
                    raise HTTPException(403, "Automation does not grant these file actions.")
            rows = [
                await self.input_version(session, context, id) for id in request.input_version_ids
            ]
            by_id = {row.id: row for row in rows}
            for output in request.outputs:
                if output.parent_version_id and output.parent_version_id not in by_id:
                    raise ValueError("An output parent must be one of the explicit input versions.")
                validate_name(output.name or Path(output.path).name)
                if Path(output.name or output.path).suffix != Path(output.path).suffix:
                    raise ValueError("Output name and declared file format must match")
            stored = [
                StoredArtifact(row.storage_key, row.size_bytes, row.sha256, row.mime_type)
                for row in rows
            ]
            # Primitive metadata is safe beyond this session's lifetime.
            parents = {str(row.id): (row.artifact_id, row.id) for row in rows}
            identities = [{"version_id": str(row.id), "sha256": row.sha256} for row in rows]
        for item in stored:
            await asyncio.to_thread(
                self.storage_service().storage.verify,
                item,
                max_bytes=self.storage_service().max_file_bytes,
            )
        return stored, parents, identities

    async def load_receipt(self, context, key, digest):
        async with self.sessions() as session:
            await fence(session)
            # Serialize duplicate invocations and metadata updates for this run.
            await session.scalar(
                select(AgentRun.id).where(AgentRun.id == context.run_id).with_for_update()
            )
            row = await session.scalar(
                select(AgentOperation).where(
                    AgentOperation.agent_run_id == context.run_id, AgentOperation.key == key
                )
            )
            if row:
                if row.fingerprint != digest:
                    raise ValueError("Operation input changed; start a new operation.")
                return row.status, row.result
            session.add(
                AgentOperation(
                    agent_run_id=context.run_id,
                    key=key,
                    fingerprint=digest,
                    status="prepared",
                    result=None,
                )
            )
            await session.commit()
            return "prepared", None

    async def save_receipt(self, context, key, *, status, result, primary=None):
        async with self.sessions() as session:
            await fence(session)
            await session.scalar(
                select(AgentRun.id).where(AgentRun.id == context.run_id).with_for_update()
            )
            row = await session.scalar(
                select(AgentOperation)
                .where(AgentOperation.agent_run_id == context.run_id, AgentOperation.key == key)
                .with_for_update()
            )
            row.status, row.result = status, result
            if primary:
                selection = await session.scalar(
                    select(AgentOperation).where(
                        AgentOperation.agent_run_id == context.run_id,
                        AgentOperation.key == PRIMARY_KEY,
                    )
                )
                if selection is None:
                    selection = AgentOperation(
                        agent_run_id=context.run_id,
                        key=PRIMARY_KEY,
                        fingerprint=CONTRACT_VERSION,
                        status="completed",
                    )
                    session.add(selection)
                selection.result = {"artifact_refs": [primary], "receipt_key": key}
            await session.commit()

    async def execute(self, context, request):
        try:
            if context.run_id is None or not context.operation_key:
                raise ValueError("Python requires a durable run and operation identity.")
            inputs, parents, identities = await self.allowed(context, request)
            digest = hashlib.sha256(
                canonical(
                    {
                        "contract": CONTRACT_VERSION,
                        "arguments": request.model_dump(mode="json"),
                        "inputs": identities,
                    }
                )
            ).hexdigest()
            key = receipt_key(context)
            status, saved = await self.load_receipt(context, key, digest)
            if status == "completed":
                return ToolResult(**saved)

            def job(stage):
                return uuid5(
                    NAMESPACE_URL,
                    f"python:{context.user_id}:{context.run_id}:{key}:{digest}:{stage}",
                )

            if status == "prepared":
                coverage = []
                for index, item in enumerate(inputs):
                    report = await self.run(
                        context, job(f"input-{index}"), stored=item, action="inspect_file"
                    )
                    preview = report.manifest.preview_manifest
                    if item.mime_type == MIME_TYPES["pdf"]:
                        coverage.append(
                            {
                                "version_id": identities[index]["version_id"],
                                "sha256": item.sha256,
                                "coverage": preview.get("coverage"),
                                "omitted_pages": preview.get("omitted_pages", []),
                            }
                        )
                        if request.outputs and preview.get("coverage") != "complete":
                            pin = next(
                                (
                                    pin
                                    for pin in request.text_only_pdf_inputs
                                    if str(pin.version_id) == identities[index]["version_id"]
                                ),
                                None,
                            )
                            if (
                                preview.get("coverage") != "partial"
                                or pin is None
                                or pin.sha256 != item.sha256
                                or list(pin.omitted_pages) != preview.get("omitted_pages")
                                or not await self.text_only_intent(context)
                            ):
                                raise ValueError(
                                    "PDF text-only clarification required. Source version: "
                                    + identities[index]["version_id"]
                                    + "; sha256: "
                                    + item.sha256
                                    + "; unreadable/image pages: "
                                    + str(preview.get("omitted_pages", []))
                                    + '. Ask the user to reply: "Đồng ý chỉ xử lý văn bản". '
                                    "Then pin this version, hash and exact omitted_pages "
                                    "in text_only_pdf_inputs. Scanned PDFs without text "
                                    "cannot be processed (no OCR)."
                                )
                generated = await self.run(
                    context,
                    job("execute"),
                    stored=inputs[0] if inputs else None,
                    additional=inputs[1:],
                    operation=ArtifactOperation(kind="python", code=request.code, python=request),
                )
                if generated.manifest.execution is None:
                    raise ValueError("Runner did not provide a Python observation")
                saved = {
                    "execution": generated.manifest.execution.model_dump(mode="json"),
                    "files": [asdict(item) for item in generated.files],
                    "pdf_inputs": coverage,
                }
                await self.save_receipt(context, key, status="executed", result=saved)

            refs, warnings = [], []
            for index, (declaration, metadata) in enumerate(
                zip(request.outputs, saved["files"], strict=False)
            ):
                operation_key = (
                    "python:"
                    + hashlib.sha256(f"{context.run_id}:{key}:{index}".encode()).hexdigest()
                )
                async with self.sessions() as session:
                    existing = await session.scalar(
                        select(ArtifactVersion).where(
                            ArtifactVersion.owner_user_id == context.user_id,
                            ArtifactVersion.operation_key == operation_key,
                        )
                    )
                    if existing:
                        refs.append(published(existing).model_dump(mode="json"))
                        continue
                current = StoredArtifact(**metadata)
                inspection = await self.run(
                    context, job(f"output-{index}"), stored=current, action="inspect_file"
                )
                preview = inspection.manifest.preview_manifest
                calculation = inspection.manifest.calculation
                warnings.extend(calculation.warnings)
                warnings.extend(preview.get("warnings", []))
                companions = ()
                if current.mime_type in {MIME_TYPES["docx"], MIME_TYPES["pdf"]}:
                    from backend.services.artifacts.previews import stage_preview

                    cleanup = set()

                    async def render(stage, index=index, **kwargs):
                        return await self.run(context, job(f"output-{index}-{stage}"), **kwargs)

                    # Rendering creates companion assets, never replaces the generated bytes.
                    preview, companions = await stage_preview(
                        self.storage_service().storage, current, render, cleanup
                    )
                parent = parents.get(str(declaration.parent_version_id))
                staged = ValidatedArtifact(
                    current,
                    declaration.name or Path(declaration.path).name,
                    calculation_status=calculation.status,
                    preview_manifest=preview,
                    source_run_id=context.run_id,
                    companions=companions,
                    companion_sources=[{"role": "operation_fingerprint", "sha256": digest}],
                )
                await self.check(context)
                async with self.sessions() as session:
                    await fence(session)
                    ref = await self.storage_service().publish_version(
                        session,
                        context.user_id,
                        artifact_id=parent[0] if parent else None,
                        parent_version_id=parent[1] if parent else None,
                        staged=staged,
                        operation_key=operation_key,
                        retain_source_on_failure=True,
                    )
                    refs.append(ref.model_dump(mode="json"))
            execution = saved["execution"]
            primary = None
            if refs:
                path = request.primary_output or request.outputs[0].path
                primary = refs[[item.path for item in request.outputs].index(path)]
            data = {
                "execution": execution,
                "outputs": refs,
                "artifact_refs": [primary] if primary else [],
                "warnings": list(dict.fromkeys(warnings)),
                "pdf_inputs": saved["pdf_inputs"],
            }
            content = json.dumps(
                {
                    "execution": execution,
                    "outputs": refs,
                    "warnings": data["warnings"],
                    "pdf_inputs": saved["pdf_inputs"],
                },
                ensure_ascii=False,
            )
            result = ToolResult(
                content=content,
                data=data,
                is_error=execution["status"] != "success",
                error_code="python_execution_error" if execution["status"] != "success" else None,
            )
            await self.save_receipt(
                context,
                key,
                status="completed",
                result={
                    "content": result.content,
                    "data": dict(result.data),
                    "is_error": result.is_error,
                    "error_code": result.error_code,
                },
                primary=primary,
            )
            return result
        except (HTTPException, ValueError, ArtifactStorageError, httpx.HTTPError) as error:
            return self.failure(error)
