import io
import os
import time
from uuid import uuid4

import pytest
from test_artifact_versions import artifact_db  # noqa: F401

from backend.models.artifact import ArtifactVersion
from backend.services.artifacts.maintenance import reconcile_orphans
from backend.services.artifacts.validation import ValidatedArtifact


def bundle(service, owner):
    from backend.services.artifacts.validation import ValidatedCompanion

    primary = service.storage.put(
        io.BytesIO(b"pdf bytes"), f"{owner}/{uuid4()}/{uuid4()}/content.pdf", max_bytes=100
    )
    source = service.storage.put(
        io.BytesIO(b"docx bytes"), f"{owner}/{uuid4()}/{uuid4()}/content.docx", max_bytes=100
    )
    return ValidatedArtifact(
        primary, "report.pdf", companions=(ValidatedCompanion("editable_source", source),)
    )


async def publish(sessions, service, owner, staged, key="bundle"):
    async with sessions() as session:
        return await service.publish_version(
            session,
            owner,
            artifact_id=None,
            parent_version_id=None,
            staged=staged,
            operation_key=key,
        )


async def test_companion_bytes_count_toward_quota_atomically(artifact_db):  # noqa: F811
    sessions, service, owner, _ = artifact_db
    staged = bundle(service, owner)
    service.max_user_bytes = staged.stored.size_bytes + staged.companions[0].stored.size_bytes - 1
    with pytest.raises(ValueError, match="quota"):
        await publish(sessions, service, owner, staged)
    assert not service.storage.path(staged.stored.key).exists()
    assert not service.storage.path(staged.companions[0].stored.key).exists()


async def test_committed_bundle_survives_ambiguous_commit_and_orphan_sweep(
    artifact_db,  # noqa: F811
    monkeypatch,
):
    sessions, service, owner, _ = artifact_db
    staged = bundle(service, owner)
    async with sessions() as session:
        original = session.commit

        async def lost_response():
            await original()
            raise ConnectionError("Lost commit response")

        monkeypatch.setattr(session, "commit", lost_response)
        with pytest.raises(ConnectionError):
            await service.publish_version(
                session,
                owner,
                artifact_id=None,
                parent_version_id=None,
                staged=staged,
                operation_key="ambiguous",
            )
    for stored in [staged.stored, staged.companions[0].stored]:
        assert service.storage.path(stored.key).exists()
        old = time.time() - 90000
        os.utime(service.storage.path(stored.key), (old, old))
    assert await reconcile_orphans(sessions, service.storage) == 0


async def test_bundle_replay_reclaims_only_new_staging(artifact_db):  # noqa: F811
    sessions, service, owner, _ = artifact_db
    staged = bundle(service, owner)
    first = await publish(sessions, service, owner, staged)
    retry = bundle(service, owner)
    second = await publish(sessions, service, owner, retry)
    assert first == second
    assert not service.storage.path(retry.stored.key).exists()
    assert not service.storage.path(retry.companions[0].stored.key).exists()
    async with sessions() as session:
        row = await session.get(ArtifactVersion, first.version_id)
        assert row.companion_sources[0]["version_id"] == str(row.id)
    assert service.storage.path(staged.companions[0].stored.key).exists()


async def test_existing_companions_are_included_in_next_quota_check(artifact_db):  # noqa: F811
    from backend.services.artifacts.service import storage_usage

    sessions, service, owner, _ = artifact_db
    first = bundle(service, owner)
    await publish(sessions, service, owner, first)
    total = first.stored.size_bytes + first.companions[0].stored.size_bytes
    async with sessions() as session:
        assert await storage_usage(session, owner) == total
    service.max_user_bytes = total * 2 - 1
    second = bundle(service, owner)
    with pytest.raises(ValueError, match="quota"):
        await publish(sessions, service, owner, second, key="second")
