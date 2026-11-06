from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from google_fixtures import google_db  # noqa: F401
from pydantic import ValidationError


def draft(**changes):
    return {
        "name": "Morning report",
        "instruction": "Summarize selected inputs",
        "schedule": {"kind": "daily", "timezone": "Asia/Ho_Chi_Minh", "local_time": "08:00"},
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"instruction": "  "},
        {"schedule": {"kind": "daily", "timezone": "invalid", "local_time": "08:00"}},
        {"schedule": {"kind": "once", "timezone": "UTC", "at": "2026-11-08T08:00:00"}},
        {"grants": [{"connection_id": str(uuid4()), "file_id": "x", "action": "delete"}]},
        {"grants": [{"connection_id": str(uuid4()), "file_id": "x", "action": "update_range"}]},
        {
            "grants": [
                {
                    "connection_id": str(uuid4()),
                    "file_id": "x",
                    "action": "update_range",
                    "range_a1": "bad",
                }
            ]
        },
    ],
)
def test_invalid_drafts_are_rejected(changes):
    from backend.schemas.automation import AutomationDraft

    with pytest.raises(ValidationError):
        AutomationDraft.model_validate(draft(**changes))


async def test_api_owner_revision_immutability_and_invalid_grants(google_db, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from backend.dependencies import get_db_session, get_optional_user
    from backend.models import User
    from backend.routes import automations

    db = google_db
    monkeypatch.setattr(
        automations, "get_settings", lambda: SimpleNamespace(automations_enabled=True)
    )
    app = FastAPI()
    app.include_router(automations.router)

    async def session_dep():
        async with db.sessions() as session:
            yield session

    actor = db.owner

    async def user_dep():
        async with db.sessions() as session:
            return await session.get(User, actor)

    app.dependency_overrides[get_db_session] = session_dep
    app.dependency_overrides[get_optional_user] = user_dep
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post("/api/automations", json=draft())
        assert created.status_code == 200, created.text
        original = created.json()
        assert not original["enabled"] and original["next_run_at"] is None
        identity = original["id"]
        changed = await client.patch(
            f"/api/automations/{identity}", json=draft(instruction="new instruction")
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["revision_id"] != original["revision_id"]
        from backend.models.automation import AutomationRevision

        async with db.sessions() as session:
            old = await session.get(AutomationRevision, original["revision_id"])
            assert old.instruction == "Summarize selected inputs"
        forged = await client.post(
            "/api/automations",
            json=draft(
                grants=[
                    {
                        "connection_id": str(uuid4()),
                        "file_id": "unselected",
                        "action": "read_sheet",
                        "range_a1": "Sheet1!A1:B100",
                    "sheet_id": 0,
                    }
                ]
            ),
        )
        assert forged.status_code == 404
        actor = db.other
        assert (await client.get(f"/api/automations/{identity}")).status_code == 404
        assert (await client.patch(f"/api/automations/{identity}", json=draft())).status_code == 404
        assert (await client.get(f"/api/automations/{identity}/runs")).status_code == 404
        assert (await client.get("/api/automations")).json()["items"] == []
        actor = db.owner
        assert (
            await client.post(
                "/api/automations", json=draft(), headers={"Origin": "https://evil.test"}
            )
        ).status_code == 403
