import asyncio
from dataclasses import replace

import pytest
from google_fixtures import google_db  # noqa: F401
from test_google_file_access import selected
from test_google_sheets import approved


async def proposal_for(db, key="write"):
    from backend.schemas.google import SheetChange
    from backend.services.google.sheets import GoogleSheets

    access, context, ref = await selected(db)
    context = replace(context, operation_key=key)
    sheets = GoogleSheets(access, db.sessions)
    proposal = await sheets.prepare(
        context,
        ref,
        SheetChange(
            kind="append_rows",
            range_a1="Sheet1!A1:B100",
            rows=[["https://new.example/repo", "New"]],
        ),
    )
    return sheets, context, ref, proposal


async def test_changed_snapshot_requires_new_approval(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    sheets, context, _, proposal = await proposal_for(db)
    db.provider.sheet_values[1][1] = "Changed externally"
    with pytest.raises(GoogleServiceError, match="snapshot|changed|approval"):
        await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
    assert db.provider.writes == []


async def test_sent_but_lost_response_is_unknown_no_retry(google_db):  # noqa: F811
    from backend.models import GoogleMutation
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    sheets, context, _, proposal = await proposal_for(db)
    db.provider.lose_write_response = True
    for _ in range(2):
        with pytest.raises(GoogleServiceError) as error:
            await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
        assert error.value.code == "google_outcome_unknown"
    assert len(db.provider.writes) == 1
    async with db.sessions() as session:
        assert (await session.get(GoogleMutation, proposal.id)).status == "unknown"
    # A new model tool-call ID must not turn an ambiguous append into a duplicate.
    from backend.schemas.google import GoogleFileRef, SheetChange

    async with db.sessions() as session:
        row = await session.get(GoogleMutation, proposal.id)
        ref = GoogleFileRef.model_validate(row.payload["request"]["ref"])
    with pytest.raises(GoogleServiceError) as blocked:
        await sheets.prepare(
            replace(context, operation_key="new-key-after-unknown"),
            ref,
            SheetChange(
                kind="append_rows",
                range_a1="Sheet1!A1:B100",
                rows=[["https://new.example/repo", "New"]],
            ),
        )
    assert blocked.value.code == "google_outcome_unknown"


async def test_replayed_completed_write_reuses_receipt(google_db):  # noqa: F811
    sheets, context, _, proposal = await proposal_for(google_db)
    first = await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
    second = await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
    assert first == second and len(google_db.provider.writes) == 1


async def test_scope_revoke_and_forged_preview_block_execution(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    sheets, context, ref, proposal = await proposal_for(db)
    with pytest.raises(GoogleServiceError):
        await sheets.execute(context, **proposal.commit_arguments())
    forged = proposal.commit_arguments()
    forged["preview"] = {**forged["preview"], "after": "Different data"}
    with pytest.raises(GoogleServiceError):
        await sheets.execute(approved(context, proposal), **forged)
    async with db.sessions() as session:
        await sheets.access.revoke_file(session, db.owner, ref.connection_id, ref.file_id)
    with pytest.raises(GoogleServiceError):
        await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
    assert db.provider.writes == []


async def test_concurrent_same_key_publishes_one_proposal_and_one_write(google_db):  # noqa: F811
    from backend.schemas.google import SheetChange
    from backend.services.google.errors import GoogleServiceError
    from backend.services.google.sheets import GoogleSheets

    db = google_db
    access, context, ref = await selected(db)
    context = replace(context, operation_key="same-key")
    sheets = GoogleSheets(access, db.sessions)
    change = SheetChange(kind="append_rows", range_a1="Sheet1!A1:B100", rows=[["new", "value"]])
    left, right = await asyncio.gather(
        sheets.prepare(context, ref, change), sheets.prepare(context, ref, change)
    )
    assert left.id == right.id
    results = await asyncio.gather(
        sheets.execute(approved(context, left), **left.commit_arguments()),
        sheets.execute(approved(context, right), **right.commit_arguments()),
        return_exceptions=True,
    )
    assert any(not isinstance(result, Exception) for result in results)
    assert all(
        not isinstance(result, Exception) or isinstance(result, GoogleServiceError)
        for result in results
    )
    assert len(db.provider.writes) == 1


async def test_failed_receipt_commit_keeps_unknown_without_replaying(google_db):  # noqa: F811
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.models import GoogleMutation
    from backend.services.google.errors import GoogleServiceError

    db = google_db
    sheets, context, _, proposal = await proposal_for(db)
    original = AsyncSession.commit
    fired = False

    async def commit(session):
        nonlocal fired
        if not fired and any(
            isinstance(row, GoogleMutation) and row.status == "completed"
            for row in session.identity_map.values()
        ):
            fired = True
            raise RuntimeError("Synthetic receipt transaction failure")
        return await original(session)

    from unittest.mock import patch

    with patch.object(AsyncSession, "commit", commit):
        with pytest.raises(GoogleServiceError) as error:
            await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
        assert error.value.code == "google_outcome_unknown"
    assert fired and len(db.provider.writes) == 1
    with pytest.raises(GoogleServiceError):
        await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
    assert len(db.provider.writes) == 1
