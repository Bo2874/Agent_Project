"""Exercise the deployed HTTP app and clean up its temporary users afterward."""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import uuid4

import httpx
from sqlalchemy import delete

from backend.database import SessionFactory, engine
from backend.models import User


class E2ESmokeError(RuntimeError):
    pass


def expect_status(response: httpx.Response, expected: int, step: str) -> None:
    if response.status_code == expected:
        return
    detail = response.text[:500]
    raise E2ESmokeError(
        f"{step}: expected HTTP {expected}, received {response.status_code}: {detail}"
    )


async def cleanup_users(emails: list[str]) -> None:
    async with SessionFactory() as session:
        await session.execute(delete(User).where(User.email.in_(emails)))
        await session.commit()


async def run(base_url: str, *, with_llm: bool) -> dict[str, object]:
    suffix = uuid4().hex
    email_a = f"e2e-a-{suffix}@example.com"
    email_b = f"e2e-b-{suffix}@example.com"
    password = f"E2e@{suffix[:12]}"
    emails = [email_a, email_b]
    report: dict[str, object] = {
        "health": False,
        "registration": False,
        "authentication": False,
        "conversation": False,
        "tenant_isolation": False,
        "llm_stream": "skipped",
        "cleanup": False,
    }
    timeout = httpx.Timeout(180 if with_llm else 30)
    try:
        async with (
            httpx.AsyncClient(base_url=base_url, timeout=timeout) as client_a,
            httpx.AsyncClient(base_url=base_url, timeout=timeout) as client_b,
        ):
            expect_status(await client_a.get("/health/live"), 200, "liveness")
            expect_status(await client_a.get("/health/ready"), 200, "readiness")
            report["health"] = True

            for client, email, name in (
                (client_a, email_a, "E2E User A"),
                (client_b, email_b, "E2E User B"),
            ):
                response = await client.post(
                    "/api/auth/register",
                    json={"email": email, "password": password, "full_name": name},
                )
                expect_status(response, 201, f"register {name}")
            report["registration"] = True

            expect_status(await client_a.get("/api/auth/me"), 200, "authenticated me")
            conversation_response = await client_a.post(
                "/api/conversations",
                json={"title": "E2E smoke conversation"},
            )
            expect_status(conversation_response, 201, "create conversation")
            conversation_id = conversation_response.json()["id"]
            conversations = await client_a.get("/api/conversations")
            expect_status(conversations, 200, "list conversations")
            if conversation_id not in {item["id"] for item in conversations.json()}:
                raise E2ESmokeError("Conversation vừa tạo không xuất hiện trong danh sách")
            report["conversation"] = True

            cross_user = await client_b.get(f"/api/conversations/{conversation_id}/messages")
            expect_status(cross_user, 404, "cross-user conversation access")
            report["tenant_isolation"] = True

            expect_status(await client_a.post("/api/auth/logout"), 204, "logout")
            expect_status(await client_a.get("/api/auth/me"), 401, "me after logout")
            expect_status(
                await client_a.post(
                    "/api/auth/login",
                    json={"email": email_a, "password": password},
                ),
                200,
                "login",
            )
            expect_status(await client_a.get("/api/auth/me"), 200, "me after login")
            report["authentication"] = True

            if with_llm:
                async with client_a.stream(
                    "POST",
                    f"/api/conversations/{conversation_id}/messages",
                    json={"content": "Trả lời đúng một từ: OK"},
                ) as response:
                    expect_status(response, 200, "chat stream")
                    body = (await response.aread()).decode("utf-8", errors="replace")
                if "event: error" in body or "event: done" not in body:
                    raise E2ESmokeError(f"Chat stream không hoàn tất: {body[-500:]}")
                report["llm_stream"] = "passed"
        return report
    finally:
        await cleanup_users(emails)
        report["cleanup"] = True
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chạy E2E smoke test qua HTTP thật.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Gọi thêm DeepSeek thật; sẽ sử dụng token API.",
    )
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url.rstrip("/"), with_llm=args.with_llm))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
