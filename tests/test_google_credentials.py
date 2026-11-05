import asyncio

import pytest
from google_fixtures import connect, expire, google_db  # noqa: F401


async def test_refresh_is_serialized_keeps_refresh_and_binds_owner(google_db):  # noqa: F811
    from backend.models.google_connection import GoogleConnection
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    service, view = await connect(db)
    await expire(db, view.id)

    async def access():
        async with db.sessions() as session:
            return await service.access_token(session, db.owner, view.id)

    assert await asyncio.gather(access(), access()) == ["fresh-access-canary"] * 2
    assert db.provider.refresh_count == 1
    async with db.sessions() as session:
        row = await session.get(GoogleConnection, view.id)
        assert b"refresh-canary" not in row.ciphertext
        assert service.decrypt(row)["refresh_token"] == "refresh-canary"
        with pytest.raises(GoogleServiceError) as error:
            await service.access_token(session, db.other, view.id)
        assert error.value.status_code == 404
        row.owner_id = db.other
        with pytest.raises(GoogleServiceError):
            service.decrypt(row)


async def test_invalid_grant_marks_reconnect_without_token_leak(google_db, caplog):  # noqa: F811
    from backend.models.google_connection import GoogleConnection
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    service, view = await connect(db)
    await expire(db, view.id)
    db.provider.invalid_grant = True
    async with db.sessions() as session:
        with pytest.raises(GoogleServiceError):
            await service.access_token(session, db.owner, view.id)
    async with db.sessions() as session:
        row = await session.get(GoogleConnection, view.id)
        assert row.status == "reconnect" and not row.offline_ready
        assert not row.ciphertext
    assert "refresh-canary" not in caplog.text
