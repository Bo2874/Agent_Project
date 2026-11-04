import asyncio
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.models import AgentRun, Conversation, DocumentVersion, User
from backend.models.artifact import Artifact, ArtifactVersion
from backend.schemas.artifact import PublishedArtifact
from backend.services.artifacts.storage import ArtifactStorage, ArtifactStorageError
from backend.services.artifacts.validation import ValidatedArtifact, validate_name

logger = logging.getLogger(__name__)


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
    ) -> PublishedArtifact:
        # Only server-generated paths belonging to this owner may enter cleanup/publication.
        key = staged.stored.key
        if key.split("/", 1)[0] != str(owner):
            raise HTTPException(404, "Artifact not found.")
        try:
            name = validate_name(staged.name)
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
                if (
                    previous.sha256 != staged.stored.sha256
                    or previous.name != name
                    or previous.parent_version_id != parent_version_id
                    or (artifact_id is not None and previous.artifact_id != artifact_id)
                ):
                    raise HTTPException(409, "Artifact operation key was used for another output.")
                result = published(previous)
                await session.commit()
                if previous.storage_key != key:
                    await asyncio.to_thread(self.storage.delete, key)
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
            used = await session.scalar(
                select(func.coalesce(func.sum(ArtifactVersion.size_bytes), 0)).where(
                    ArtifactVersion.owner_user_id == owner,
                )
            )
            if used + staged.stored.size_bytes > self.max_user_bytes:
                raise ArtifactStorageError("Artifact storage quota exceeded.", 413)
            row = ArtifactVersion(
                id=uuid4(),
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
                preview_manifest=staged.preview_manifest,
                companion_sources=staged.companion_sources,
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
                    await asyncio.to_thread(self.storage.delete, key)
                await session.rollback()
            except Exception:
                logger.warning("Artifact staging cleanup deferred", exc_info=True)
            raise


def get_artifact_service() -> ArtifactService:
    settings = get_settings()
    return ArtifactService(
        ArtifactStorage(settings.artifact_storage_path),
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
