import asyncio
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import BigInteger, cast, column, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.models import AgentRun, Conversation, DocumentVersion, User
from backend.models.artifact import Artifact, ArtifactVersion
from backend.schemas.artifact import PublishedArtifact
from backend.services.artifacts.storage import MIME_TYPES, ArtifactStorage, ArtifactStorageError
from backend.services.artifacts.validation import (
    ValidatedArtifact,
    validate_name,
    validate_preview_png,
)

logger = logging.getLogger(__name__)
BINARY_ROLES = {"editable_source", "preview_pdf", "preview_page"}


async def storage_usage(session, owner):
    records = func.jsonb_array_elements(ArtifactVersion.companion_sources).table_valued(
        column("value", JSONB)
    )
    extra = (
        select(func.coalesce(func.sum(cast(records.c.value["size_bytes"].astext, BigInteger)), 0))
        .where(records.c.value["role"].astext.in_(BINARY_ROLES))
        .correlate(ArtifactVersion)
        .scalar_subquery()
    )
    return await session.scalar(
        select(func.coalesce(func.sum(ArtifactVersion.size_bytes + extra), 0)).where(
            ArtifactVersion.owner_user_id == owner
        )
    )


def editable_source(row):
    from backend.services.artifacts.storage import StoredArtifact

    items = [item for item in row.companion_sources if item.get("role") == "editable_source"]
    if not items:
        return None
    item = items[0]
    if (
        len(items) != 1
        or item.get("version_id") != str(row.id)
        or (
            item["storage_key"].split("/", 1)[0] != str(row.owner_user_id)
            or item["mime_type"] != MIME_TYPES["docx"]
        )
    ):
        raise ArtifactStorageError("Invalid editable source version binding")
    return StoredArtifact(
        item["storage_key"], item["size_bytes"], item["sha256"], item["mime_type"]
    )


def published(row: ArtifactVersion) -> PublishedArtifact:
    return PublishedArtifact(
        artifact_id=row.artifact_id,
        version_id=row.id,
        parent_version_id=row.parent_version_id,
        name=row.name,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        calculation_status=row.calculation_status,
    )


class ArtifactService:
    def __init__(
        self, storage: ArtifactStorage, *, max_file_bytes=20971520, max_user_bytes=209715200
    ):
        self.storage = storage
        self.max_file_bytes = max_file_bytes
        self.max_user_bytes = max_user_bytes

    async def open_owned_version(
        self,
        session: AsyncSession,
        owner: UUID,
        version_id: UUID,
    ) -> ArtifactVersion:
        row = await session.scalar(
            select(ArtifactVersion).where(
                ArtifactVersion.id == version_id,
                ArtifactVersion.owner_user_id == owner,
            )
        )
        if row is None:
            raise HTTPException(404, "Artifact version not found.")
        return row

    async def publish_version(
        self,
        session: AsyncSession,
        owner: UUID,
        *,
        artifact_id: UUID | None,
        parent_version_id: UUID | None,
        staged: ValidatedArtifact,
        operation_key: str,
        retain_source_on_failure: bool = False,
    ) -> PublishedArtifact:
        # Only server-generated paths belonging to this owner may enter cleanup/publication.
        key = staged.stored.key
        if key.split("/", 1)[0] != str(owner):
            raise HTTPException(404, "Artifact not found.")
        owned_keys = {key} | {
            item.stored.key
            for item in staged.companions
            if item.stored.key.split("/", 1)[0] == str(owner)
        }
        try:
            if len(staged.companions) > 22 or any(
                item.stored.key.split("/", 1)[0] != str(owner) for item in staged.companions
            ):
                raise HTTPException(404, "Companion not found.")
            if any(
                item.get("role") in BINARY_ROLES or "storage_key" in item
                for item in staged.companion_sources
            ):
                raise ArtifactStorageError("Binary companions require verified staging")
            expected_types = {
                "editable_source": MIME_TYPES["docx"],
                "preview_pdf": MIME_TYPES["pdf"],
                "preview_page": MIME_TYPES["png"],
            }
            identities = []
            for companion in staged.companions:
                if (
                    companion.role not in expected_types
                    or companion.stored.mime_type != expected_types[companion.role]
                ):
                    raise ArtifactStorageError("Invalid companion MIME type")
                if companion.role == "preview_page" and not (
                    companion.page and 1 <= companion.page <= 20
                ):
                    raise ArtifactStorageError("Invalid preview page number")
                identities.append((companion.role, companion.page))
                await asyncio.to_thread(
                    self.storage.verify, companion.stored, max_bytes=self.max_file_bytes
                )
                if companion.role == "preview_page":
                    await asyncio.to_thread(
                        validate_preview_png, self.storage.path(companion.stored.key)
                    )
            if len(set(identities)) != len(identities) or len(owned_keys) != 1 + len(
                staged.companions
            ):
                raise ArtifactStorageError("Duplicate companion identity or storage key")
            name = validate_name(staged.name)
            from pathlib import Path

            if MIME_TYPES[Path(name).suffix.lower()[1:]] != staged.stored.mime_type:
                raise ArtifactStorageError("Artifact filename and MIME type do not match.")
            if not 1 <= len(operation_key) <= 160:
                raise HTTPException(422, "Invalid artifact operation key.")
            await asyncio.to_thread(
                self.storage.verify,
                staged.stored,
                max_bytes=self.max_file_bytes,
            )
            # Owner lock serializes quota accounting and operation-key replay, including creates.
            user = await session.scalar(select(User).where(User.id == owner).with_for_update())
            if user is None:
                raise HTTPException(404, "Account not found.")
            previous = await session.scalar(
                select(ArtifactVersion).where(
                    ArtifactVersion.owner_user_id == owner,
                    ArtifactVersion.operation_key == operation_key,
                )
            )
            if previous:
                old_bundle = sorted(
                    (item["role"], item["sha256"], item["size_bytes"], item.get("page") or 0)
                    for item in previous.companion_sources
                    if item.get("role") in BINARY_ROLES
                )
                new_bundle = sorted(
                    (item.role, item.stored.sha256, item.stored.size_bytes, item.page or 0)
                    for item in staged.companions
                )
                if (
                    previous.sha256 != staged.stored.sha256
                    or previous.name != name
                    or previous.parent_version_id != parent_version_id
                    or (artifact_id is not None and previous.artifact_id != artifact_id)
                    or old_bundle != new_bundle
                ):
                    raise HTTPException(409, "Artifact operation key was used for another output.")
                result = published(previous)
                await session.commit()
                retained = {previous.storage_key} | {
                    item["storage_key"]
                    for item in previous.companion_sources
                    if item.get("role") in BINARY_ROLES
                }
                for unused in owned_keys - retained:
                    await asyncio.to_thread(self.storage.delete, unused)
                return result
            if (artifact_id is None) != (parent_version_id is None):
                raise HTTPException(422, "Editing requires an artifact and a parent version.")
            if artifact_id is None:
                artifact = Artifact(
                    id=uuid4(), owner_user_id=owner, name=name, next_version_number=1
                )
                session.add(artifact)
                await session.flush()
            else:
                artifact = await session.scalar(
                    select(Artifact)
                    .where(
                        Artifact.id == artifact_id,
                        Artifact.owner_user_id == owner,
                    )
                    .with_for_update()
                )
                if artifact is None:
                    raise HTTPException(404, "Artifact not found.")
                parent = await self.open_owned_version(session, owner, parent_version_id)
                if parent.artifact_id != artifact.id:
                    raise HTTPException(404, "Parent version not found.")
            if staged.source_document_version_id:
                source = await session.scalar(
                    select(DocumentVersion).where(
                        DocumentVersion.id == staged.source_document_version_id,
                        DocumentVersion.document_id == staged.source_document_id,
                        DocumentVersion.owner_user_id == owner,
                    )
                )
                if source is None:
                    raise HTTPException(404, "Source document not found.")
            if staged.source_run_id:
                source_run = await session.scalar(
                    select(AgentRun.id)
                    .join(
                        Conversation,
                        Conversation.id == AgentRun.conversation_id,
                    )
                    .where(AgentRun.id == staged.source_run_id, Conversation.user_id == owner)
                )
                if source_run is None:
                    raise HTTPException(404, "Source run not found.")
            used = await storage_usage(session, owner)
            if (
                used
                + staged.stored.size_bytes
                + sum(item.stored.size_bytes for item in staged.companions)
                > self.max_user_bytes
            ):
                raise ArtifactStorageError("Artifact storage quota exceeded.", 413)
            version_id = uuid4()
            preview = staged.preview_manifest
            if preview.get("kind") == "pages":
                pages = [item for item in staged.companions if item.role == "preview_page"]
                if (
                    preview.get("source_sha256") != staged.stored.sha256
                    or sorted(item.page for item in pages)
                    != list(range(1, preview.get("page_count", 0) + 1))
                    or {str(item.asset_id) for item in pages}
                    != {item.get("asset_id") for item in preview.get("assets", [])}
                ):
                    raise ArtifactStorageError("Incomplete preview version bundle")
                preview = {**preview, "source_version_id": str(version_id)}
            row = ArtifactVersion(
                id=version_id,
                artifact_id=artifact.id,
                owner_user_id=owner,
                parent_version_id=parent_version_id,
                version_number=artifact.next_version_number,
                name=name,
                origin=staged.origin,
                storage_key=key,
                mime_type=staged.stored.mime_type,
                size_bytes=staged.stored.size_bytes,
                sha256=staged.stored.sha256,
                operation_key=operation_key,
                source_document_id=staged.source_document_id,
                source_document_version_id=staged.source_document_version_id,
                source_run_id=staged.source_run_id,
                calculation_status=staged.calculation_status,
                preview_manifest=preview,
                companion_sources=staged.companion_sources
                + [item.record(version_id) for item in staged.companions],
            )
            artifact.next_version_number += 1
            session.add(row)
            await session.flush()
            result = published(row)
            await session.commit()
            return result
        except BaseException:
            await session.rollback()
            # A lost commit response is ambiguous. Never delete a committed output; if the
            # database is unavailable, leave the orphan to a delayed reconciliation sweep.
            try:
                referenced = await session.scalar(
                    select(ArtifactVersion.id).where(
                        ArtifactVersion.storage_key == key,
                    )
                )
                if referenced is None:
                    for unused in owned_keys:
                        # A durable Python receipt owns its immutable source until
                        # publication/recovery succeeds or terminal orphan cleanup.
                        if unused != key or not retain_source_on_failure:
                            await asyncio.to_thread(self.storage.delete, unused)
                await session.rollback()
            except Exception:
                logger.warning("Artifact staging cleanup deferred", exc_info=True)
            raise


def get_artifact_service() -> ArtifactService:
    settings = get_settings()
    from backend.core.config import PROJECT_ROOT

    root = settings.artifact_storage_path
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return ArtifactService(
        ArtifactStorage(root),
        max_file_bytes=settings.artifact_max_file_bytes,
        max_user_bytes=settings.artifact_max_user_bytes,
    )


async def open_owned_version(
    session: AsyncSession, owner: UUID, version_id: UUID
) -> ArtifactVersion:
    return await get_artifact_service().open_owned_version(session, owner, version_id)


async def publish_version(
    session: AsyncSession,
    owner: UUID,
    *,
    artifact_id: UUID | None,
    parent_version_id: UUID | None,
    staged: ValidatedArtifact,
    operation_key: str,
) -> PublishedArtifact:
    return await get_artifact_service().publish_version(
        session,
        owner,
        artifact_id=artifact_id,
        parent_version_id=parent_version_id,
        staged=staged,
        operation_key=operation_key,
    )
