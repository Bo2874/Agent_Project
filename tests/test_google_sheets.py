from dataclasses import replace

import pytest
from google_fixtures import google_db  # noqa: F401
from test_google_file_access import selected


def approved(context, proposal):
    from agent_core.tools.approval import approval_fingerprint

    tool = (
        "google_sheets_append"
        if proposal.preview.operation == "append_rows"
        else "google_sheets_update"
    )
    return replace(
        context, approved_call=(tool, approval_fingerprint(tool, proposal.commit_arguments()))
    )


async def test_append_raw_preserves_equal_sign_as_text(google_db):  # noqa: F811
    from backend.schemas.google import SheetChange
    from backend.services.google.sheets import GoogleSheets

    db = google_db
    access, context, ref = await selected(db)
    sheets = GoogleSheets(access, db.sessions)
    snapshot = await sheets.read(context, ref, range_a1="Sheet1!A1:B100", limit=1)
    assert snapshot.values == [["URL", "Topic"]] and snapshot.next_offset == 1
    proposal = await sheets.prepare(
        replace(context, operation_key="append"),
        ref,
        SheetChange(
            kind="append_rows",
            range_a1="Sheet1!A1:B100",
            rows=[['=HYPERLINK("https://example.com")', "Raw text"]],
        ),
    )
    receipt = await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
    assert receipt.updated_rows == 1
    assert db.provider.writes[0]["params"]["valueInputOption"] == "RAW"
    assert db.provider.sheet_values[-1][0].startswith("=HYPERLINK")


async def test_explicit_formulas_formatting_and_dedup(google_db):  # noqa: F811
    from backend.schemas.google import SheetChange
    from backend.services.google.sheets import GoogleSheets

    db = google_db
    access, context, ref = await selected(db)
    sheets = GoogleSheets(access, db.sessions)
    for key, change in [
        (
            "formula",
            {
                "kind": "set_formulas",
                "range_a1": "Sheet1!A3:B3",
                "rows": [["=SUM(1,2)", "=SUM(3,4)"]],
            },
        ),
        (
            "format",
            {
                "kind": "format_range",
                "range_a1": "Sheet1!A1:B2",
                "format": {"bold": True, "background": "#ffeeaa"},
            },
        ),
        (
            "dedup",
            {
                "kind": "append_rows",
                "range_a1": "Sheet1!A1:B100",
                "rows": [
                    ["https://existing.example/repo", "Duplicate"],
                    ["https://new.example/repo", "New"],
                    ["https://new.example/repo", "Duplicate input"],
                ],
                "dedup_column": 0,
            },
        ),
    ]:
        proposal = await sheets.prepare(
            replace(context, operation_key=key), ref, SheetChange(**change)
        )
        await sheets.execute(approved(context, proposal), **proposal.commit_arguments())
    assert db.provider.writes[0]["params"]["valueInputOption"] == "USER_ENTERED"
    request = db.provider.writes[1]["body"]["requests"][0]["repeatCell"]
    assert (
        request["range"]["sheetId"] == 0
        and request["cell"]["userEnteredFormat"]["textFormat"]["bold"]
    )
    assert db.provider.writes[-1]["body"]["values"] == [["https://new.example/repo", "New"]]


async def test_range_and_observation_bounds(google_db):  # noqa: F811
    from backend.services.google.errors import GoogleServiceError
    from backend.services.google.sheets import GoogleSheets

    access, context, ref = await selected(google_db)
    sheets = GoogleSheets(access, google_db.sessions)
    for target in ["Sheet1!A:A", "Sheet1!A1:ZZZ1000000", "https://evil.example/A1", "Sheet1!B2:A1"]:
        with pytest.raises(GoogleServiceError):
            await sheets.read(context, ref, range_a1=target)


async def test_safe_reads_retry_bounded_but_post_never_retries():
    import httpx

    from backend.services.google.client import DRIVE_URL, GoogleHTTP
    from backend.services.google.errors import GoogleServiceError

    calls = []

    async def respond(request):
        calls.append(request.method)
        return (
            httpx.Response(503, json={"error": {"code": 503}})
            if len(calls) < 3 or request.method == "POST"
            else httpx.Response(200, json={"ok": True})
        )

    client = GoogleHTTP(httpx.MockTransport(respond))
    assert (await client.request("GET", DRIVE_URL + "/about"))["ok"]
    assert calls == ["GET"] * 3
    with pytest.raises(GoogleServiceError):
        await client.request("POST", DRIVE_URL + "/files", json={"name": "test"})
    assert calls == ["GET"] * 3 + ["POST"]
