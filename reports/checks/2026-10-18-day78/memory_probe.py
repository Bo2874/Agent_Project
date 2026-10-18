"""Exercise existing memory HTTP APIs with a synthetic manual-memory fixture."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx

from backend.core.config import get_settings
from backend.database import SessionFactory, engine
from backend.models import UserMemory
from scripts.run_e2e_smoke import cleanup_users, expect_status


async def main():
    settings = get_settings()
    assert settings.app_env == "development" and settings.db_host == "postgres"
    suffix = uuid4().hex
    emails = [f"memory-{i}-{suffix}@example.com" for i in range(2)]
    try:
        async with (
            httpx.AsyncClient(base_url="http://app:8000", timeout=30, trust_env=False) as owner,
            httpx.AsyncClient(base_url="http://app:8000", timeout=30, trust_env=False) as other,
        ):
            for client, email in zip((owner, other), emails, strict=True):
                response = await client.post(
                    "/api/auth/register",
                    json={
                        "email": email,
                        "password": f"Probe@{suffix[:12]}",
                        "full_name": "Memory Probe",
                    },
                )
                expect_status(response, 201, "register")
            me = await owner.get("/api/auth/me")
            expect_status(me, 200, "me")
            async with SessionFactory() as session:
                memory = UserMemory(
                    user_id=UUID(me.json()["id"]),
                    memory_type="preference",
                    memory_key=f"compose-probe-{suffix}",
                    content="I prefer Python examples.",
                    source_type="manual",
                    sensitivity="normal",
                    retention_until=datetime.now(UTC) + timedelta(days=1),
                )
                session.add(memory)
                await session.commit()
                await session.refresh(memory)
                memory_id = str(memory.id)
            response = await owner.get("/api/memories")
            expect_status(response, 200, "list")
            assert memory_id in {item["id"] for item in response.json()}
            response = await other.get("/api/memories")
            expect_status(response, 200, "isolation list")
            assert memory_id not in {item["id"] for item in response.json()}
            response = await other.patch(f"/api/memories/{memory_id}", json={"content": "Changed"})
            expect_status(response, 404, "isolation update")
            response = await owner.patch(
                f"/api/memories/{memory_id}", json={"content": "I prefer concise Python examples."}
            )
            expect_status(response, 200, "update")
            assert response.json()["content"] == "I prefer concise Python examples."
            memory_id = response.json()["id"]  # Edits supersede the old memory version.
            response = await owner.patch(f"/api/memories/{memory_id}/pin", json={"is_pinned": True})
            expect_status(response, 200, "pin")
            assert response.json()["is_pinned"]
            expect_status(await owner.delete(f"/api/memories/{memory_id}"), 204, "delete")
            response = await owner.get("/api/memories")
            assert memory_id not in {item["id"] for item in response.json()}
    finally:
        await cleanup_users(emails)
        await engine.dispose()
    print(
        json.dumps(
            {
                "list": True,
                "update": True,
                "pin": True,
                "delete": True,
                "tenant_isolation": True,
                "cleanup": True,
                "llm_provider_calls": 0,
            }
        )
    )


asyncio.run(main())
