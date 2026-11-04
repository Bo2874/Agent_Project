import asyncio
import hashlib
import io
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models import User
from backend.models.artifact import ArtifactVersion
from backend.services.artifacts.service import ArtifactService
from backend.services.artifacts.storage import ArtifactStorage
from backend.services.artifacts.validation import ValidatedArtifact


@pytest.fixture
async def artifact_db(tmp_path):
    url = os.environ.get("ARTIFACT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set ARTIFACT_TEST_DATABASE_URL to an isolated migrated PostgreSQL database")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, other = uuid4(), uuid4()
    async with sessions() as session:
        session.add_all(
            [User(id=id, email=f"{id}@example.com", password_hash="test") for id in (owner, other)]
        )
        await session.commit()
    service = ArtifactService(ArtifactStorage(tmp_path), max_user_bytes=1000)
    yield sessions, service, owner, other
    async with sessions() as session:
        for id in (owner, other):
            await session.delete(await session.get(User, id))
        await session.commit()
    await engine.dispose()


def stage(service, owner, content=b"original", origin="upload"):
    stored = service.storage.put(
        io.BytesIO(content),
        f"{owner}/{uuid4()}/{uuid4()}/content.xlsx",
        max_bytes=1000,
    )
    return ValidatedArtifact(stored=stored, name="budget.xlsx", origin=origin)


async def create(sessions, service, owner):
    async with sessions() as session:
        return await service.publish_version(
            session,
            owner,
            artifact_id=None,
            parent_version_id=None,
            staged=stage(service, owner),
            operation_key=str(uuid4()),
        )


async def test_other_owner_cannot_open(artifact_db):
    sessions, service, owner, other = artifact_db
    original = await create(sessions, service, owner)
    async with sessions() as session:
        with pytest.raises(HTTPException) as error:
            await service.open_owned_version(session, other, original.version_id)
    assert error.value.status_code == 404


async def test_concurrent_children_preserve_parent(artifact_db):
    sessions, service, owner, _ = artifact_db
    original = await create(sessions, service, owner)
    async with sessions() as session:
        row = await service.open_owned_version(session, owner, original.version_id)
        original_key = row.storage_key

    async def child(content):
        async with sessions() as session:
            return await service.publish_version(
                session,
                owner,
                artifact_id=original.artifact_id,
                parent_version_id=original.version_id,
                staged=stage(service, owner, content, "agent"),
                operation_key=str(uuid4()),
            )

    a, b = await asyncio.gather(child(b"child-a"), child(b"child-b"))
    assert a.version_id != b.version_id
    assert a.parent_version_id == b.parent_version_id == original.version_id
    assert (
        hashlib.sha256(service.storage.path(original_key).read_bytes()).hexdigest()
        == original.sha256
    )
    async with sessions() as session:
        numbers = (
            await session.scalars(
                select(ArtifactVersion.version_number).where(
                    ArtifactVersion.artifact_id == original.artifact_id,
                )
            )
        ).all()
    assert sorted(numbers) == [1, 2, 3]


async def test_publish_replay_same_operation(artifact_db):
    sessions, service, owner, _ = artifact_db
    operation = str(uuid4())

    async def publish():
        async with sessions() as session:
            return await service.publish_version(
                session,
                owner,
                artifact_id=None,
                parent_version_id=None,
                staged=stage(service, owner),
                operation_key=operation,
            )

    a, b = await asyncio.gather(publish(), publish())
    assert a == b
    async with sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ArtifactVersion)
            .where(
                ArtifactVersion.owner_user_id == owner,
            )
        )
    assert count == 1


async def test_failed_publication_removes_staged_file(artifact_db):
    sessions, service, owner, _ = artifact_db
    staged = stage(service, owner)
    async with sessions() as session:
        with pytest.raises(HTTPException):
            await service.publish_version(
                session,
                owner,
                artifact_id=uuid4(),
                parent_version_id=uuid4(),
                staged=staged,
                operation_key=str(uuid4()),
            )
    assert not service.storage.path(staged.stored.key).exists()


async def test_published_version_cannot_be_mutated_in_database(artifact_db):
    sessions, service, owner, _ = artifact_db
    original = await create(sessions, service, owner)
    async with sessions() as session:
        with pytest.raises(DBAPIError):
            await session.execute(
                update(ArtifactVersion)
                .where(
                    ArtifactVersion.id == original.version_id,
                )
                .values(sha256="0" * 64)
            )
        await session.rollback()


async def test_concurrent_quota_cannot_be_exceeded(artifact_db):
    sessions, service, owner, _ = artifact_db
    service.max_user_bytes = 12

    async def publish():
        async with sessions() as session:
            return await service.publish_version(
                session,
                owner,
                artifact_id=None,
                parent_version_id=None,
                staged=stage(service, owner),
                operation_key=str(uuid4()),
            )

    results = await asyncio.gather(publish(), publish(), return_exceptions=True)
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    async with sessions() as session:
        used = await session.scalar(
            select(func.sum(ArtifactVersion.size_bytes)).where(
                ArtifactVersion.owner_user_id == owner,
            )
        )
    assert used <= 12
