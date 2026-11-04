from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_artifact_api import api, upload  # noqa: F401

from backend.models import Message
from backend.models.artifact import MessageArtifact
from backend.services.artifacts.publication import bind_message_artifacts, message_artifacts


async def test_replay_binds_one_card_and_reload_uses_published_version(api):  # noqa: F811
    client, sessions, _, owner, _, conversation, _ = api
    ref = await upload(client)
    message = Message(
        id=uuid4(), conversation_id=conversation.id, role="assistant", content="Created"
    )
    async with sessions() as session:
        session.add(message)
        await session.commit()
        for _ in range(2):
            await bind_message_artifacts(session, owner.id, conversation.id, message.id, [ref])
            await session.commit()
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MessageArtifact)
                .where(MessageArtifact.message_id == message.id)
            )
            == 1
        )
    async with sessions() as session:
        cards = await message_artifacts(session, owner.id, conversation.id, [message.id])
        assert cards[message.id][0].model_dump(mode="json") == ref


async def test_foreign_owner_cannot_bind_metadata(api):  # noqa: F811
    client, sessions, _, owner, other, conversation, _ = api
    ref = await upload(client)
    async with sessions() as session:
        with pytest.raises(HTTPException) as caught:
            await bind_message_artifacts(session, other.id, conversation.id, uuid4(), [ref])
        assert caught.value.status_code == 404


async def test_preview_is_owner_scoped_and_paginated(api):  # noqa: F811
    client, _, _, _, _, _, _ = api
    ref = await upload(client)
    response = await client.get(f"/api/artifact-versions/{ref['version_id']}/preview?limit=2")
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["version_id"] == ref["version_id"]
    assert preview["preview"]["cells"][0]["value"] == "uploaded"
    assert preview["calculation_status"] == "not_calculated"
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get(f"/api/artifact-versions/{UUID(int=1)}/preview")).status_code == 404
