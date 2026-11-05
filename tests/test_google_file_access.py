from dataclasses import replace
from uuid import uuid4

import pytest
from google_fixtures import connect, google_db  # noqa: F401

from agent_core.tools import ToolContext
from backend.models import Conversation
from backend.schemas.google import GoogleFileRef


async def selected(db, file_id="sheet-a"):
    from backend.services.google.access import GoogleAccess

    oauth, view = await connect(db)
    access = GoogleAccess(oauth)
    context = ToolContext(user_id=db.owner, conversation_id=db.conversation, user_role="user")
    ref = GoogleFileRef(connection_id=view.id, file_id=file_id, context_id=db.conversation)
    async with db.sessions() as session:
        await access.set_files(session, db.owner, view.id, [file_id])
        await access.set_work_files(session, db.owner, db.conversation, [ref])
    return access, context, ref


async def test_unselected_file_rejected_even_if_google_allows(google_db):  # noqa: F811
    from backend.services.google.access import GoogleAccess
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    oauth, view = await connect(db)
    access = GoogleAccess(oauth)
    context = ToolContext(user_id=db.owner, conversation_id=db.conversation, user_role="user")
    assert context.google_file_refs == ()
    ref = GoogleFileRef(connection_id=view.id, file_id="sheet-a", context_id=db.conversation)
    async with db.sessions() as session:
        with pytest.raises(GoogleServiceError):
            await access.authorize(session, context, ref, "read_sheet")
        await access.set_files(session, db.owner, view.id, ["sheet-a"])
        with pytest.raises(GoogleServiceError):
            await access.authorize(session, context, ref, "read_sheet")
        await access.set_work_files(session, db.owner, db.conversation, [ref])
        result = await access.authorize(session, context, ref, "read_sheet")
        assert result.ref == ref and "canary" not in repr(result)


async def test_revoked_grant_or_other_connection_rejected(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    access, context, ref = await selected(db)
    async with db.sessions() as session:
        for forged in [
            ref.model_copy(update={"connection_id": uuid4()}),
            ref.model_copy(update={"context_id": uuid4()}),
        ]:
            with pytest.raises(GoogleServiceError):
                await access.authorize(session, context, forged, "read_sheet")
        with pytest.raises(GoogleServiceError):
            await access.authorize(session, replace(context, user_id=db.other), ref, "read_sheet")
        await access.revoke_file(session, db.owner, ref.connection_id, ref.file_id)
        with pytest.raises(GoogleServiceError):
            await access.authorize(session, context, ref, "read_sheet")


async def test_xlsx_is_not_native_sheet(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError

    access, context, ref = await selected(google_db, "office-a")
    async with google_db.sessions() as session:
        with pytest.raises(GoogleServiceError, match="native|type"):
            await access.authorize(session, context, ref, "read_sheet")
        with pytest.raises(GoogleServiceError):
            await access.set_files(session, google_db.owner, ref.connection_id, ["folder-a"])


async def test_selected_file_not_implicitly_shared_with_other_conversation(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    access, context, ref = await selected(db)
    other_chat = uuid4()
    async with db.sessions() as session:
        session.add(Conversation(id=other_chat, user_id=db.owner))
        await session.commit()
        with pytest.raises(GoogleServiceError):
            await access.authorize(
                session,
                replace(context, conversation_id=other_chat),
                ref.model_copy(update={"context_id": other_chat}),
                "read_sheet",
            )


async def test_reconnect_does_not_resurrect_old_chat_grants(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    access, context, ref = await selected(db)
    db.provider.account = "permission-account-b"
    await connect(db)
    async with db.sessions() as session:
        with pytest.raises(GoogleServiceError):
            await access.authorize(session, context, ref, "read_sheet")
        await access.set_files(session, db.owner, ref.connection_id, [ref.file_id])
        with pytest.raises(GoogleServiceError):
            await access.authorize(session, context, ref, "read_sheet")
        await access.set_work_files(session, db.owner, db.conversation, [ref])
        assert await access.authorize(session, context, ref, "read_sheet")
        db.provider.files.pop("sheet-a")
        with pytest.raises(GoogleServiceError):
            await access.authorize(session, context, ref, "read_sheet")


async def test_picker_and_work_selection_routes_are_owner_scoped(google_db, monkeypatch):  # noqa: F811
    import httpx
    from fastapi import FastAPI

    from backend.dependencies import get_db_session, get_optional_user
    from backend.models import User
    from backend.routes import connections

    db = google_db
    service, connection = await connect(db)
    app = FastAPI()
    connections.install(app)
    monkeypatch.setattr(connections, "get_settings", lambda: db.settings)
    monkeypatch.setattr(connections, "oauth_service", lambda: service)

    async def actor():
        async with db.sessions() as session:
            return await session.get(User, db.owner)

    async def database():
        async with db.sessions() as session:
            yield session

    app.dependency_overrides[get_optional_user] = actor
    app.dependency_overrides[get_db_session] = database
    prefix = f"/api/connections/google/{connection.id}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.put(prefix + "/files", json={"file_ids": ["../../outside"]})
        ).status_code == 422
        assert (
            await client.put(prefix + "/files", json={"file_ids": ["sheet-a"]})
        ).status_code == 200
        assert (await client.get(prefix + "/files")).json()["items"][0]["file_id"] == "sheet-a"
        ref = {
            "connection_id": str(connection.id),
            "file_id": "sheet-a",
            "context_id": str(db.conversation),
        }
        selected = await client.put(
            f"/api/conversations/{db.conversation}/google-files", json={"file_refs": [ref]}
        )
        assert selected.status_code == 200
        assert selected.json()["items"][0]["file_id"] == "sheet-a"
        forbidden = await client.put(
            f"/api/conversations/{uuid4()}/google-files", json={"file_refs": [ref]}
        )
        assert forbidden.status_code == 404
        service.settings = db.settings.model_copy(
            update={
                "google_picker_api_key": "public-picker-key",
                "google_cloud_project_number": "12345",
            }
        )
        picker = await client.post(prefix + "/picker-config", headers={"Origin": "http://test"})
        assert picker.status_code == 200 and picker.headers["cache-control"] == "no-store"
        assert "access-canary" in picker.text and "refresh-canary" not in picker.text
        assert (
            await client.post(
                prefix + "/picker-config", headers={"Origin": "https://other.example"}
            )
        ).status_code == 403
        assert (await client.delete(prefix + "/files/sheet-a")).status_code == 204
        assert (await client.get(f"/api/conversations/{db.conversation}/google-files")).json()[
            "items"
        ] == []
