"""Opt-in paid acceptance checks through real auth/chat/SSE and the configured tools.

Preview: python -m scripts.test_agent_live
Execute: python -m scripts.test_agent_live --live
Reports contain public fixture prompts/results, never credentials or auth responses.
"""

import argparse
import asyncio
import json
import secrets
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

CASES = (
    ("R01", "Chỉ trả lời đúng chuỗi AGENT_REACT_OK, không gọi công cụ."),
    (
        "R02",
        "Tôi muốn gửi email nhưng chưa biết gửi cho ai và chưa có nội dung. "
        "Bạn cần tôi bổ sung gì? Chưa được gửi email hoặc tự điền thông tin.",
    ),
    (
        "R03",
        'Dùng demo__count_text đếm số ký tự của chuỗi "abc def". Chỉ sau khi nhận kết quả, '
        "dùng demo__calculate nhân số ký tự đó với 17. Không tự đếm hay tính thay tool. "
        "Trả lời kết quả cuối.",
    ),
    (
        "R04",
        "Tìm trên website Python.org ngày phát hành chính thức Python 3.14.0. "
        "Chỉ cần một lần gọi search_web với câu hỏi này; trả lời bằng tiếng Việt kèm link nguồn.",
    ),
    (
        "R05",
        "Từ câu trả lời vừa rồi, Python 3.14.0 được phát hành năm nào? Không cần tìm lại.",
    ),
)


def parse_events(body):
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, lines = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                lines.append(line[5:].strip())
        if lines:
            events.append({"event": name, "data": json.loads("\n".join(lines))})
    return events


async def run_live():
    import httpx
    from sqlalchemy import func, select

    from backend.core.config import PROJECT_ROOT, get_settings
    from backend.database import SessionFactory
    from backend.main import app
    from backend.models import RoutingTrace

    settings = get_settings()
    if not settings.agent_react_enabled or not settings.web_search_enabled:
        raise SystemExit("Enable AGENT_REACT_ENABLED and WEB_SEARCH_ENABLED before this suite.")
    if not settings.deepseek_api_key or not settings.openrouter_api_key:
        raise SystemExit("The live suite requires configured model keys.")
    folder = (
        PROJECT_ROOT
        / "data/agent-testing"
        / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
    )
    folder.mkdir(parents=True)
    report = {
        "suite": "react-stage-1",
        "transport": "FastAPI ASGI auth/message/SSE/history; real DB, MCP, DeepSeek and web search",
        "browser_tested": False,
        "cases": [],
        "passed": False,
    }
    path = folder / "REPORT.json"

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def api(client, method, url, **kwargs):
        response = await client.request(method, url, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"{method} {url}: HTTP {response.status_code}")
        return response

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://agent-live.local",
            timeout=240,
            trust_env=False,
        ) as client:
            # Disposable fixture account, not the owner's existing conversations.
            email = f"react-live-{uuid4().hex[:12]}@example.com"
            account = (
                await api(
                    client,
                    "POST",
                    "/api/auth/register",
                    json={
                        "email": email,
                        "password": secrets.token_urlsafe(24),
                        "full_name": "ReAct live acceptance",
                    },
                )
            ).json()
            report.update(test_email=email, test_user_id=account["user"]["id"])
            conversation_id = None
            for case_id, question in CASES:
                if case_id != "R05":
                    conversation = (
                        await api(
                            client,
                            "POST",
                            "/api/conversations",
                            json={
                                "title": f"ReAct acceptance {case_id}",
                            },
                        )
                    ).json()
                    conversation_id = conversation["id"]
                item = {"id": case_id, "question": question, "conversation_id": conversation_id}
                report["cases"].append(item)
                started = perf_counter()
                try:
                    async with asyncio.timeout(240):
                        response = await api(
                            client,
                            "POST",
                            f"/api/conversations/{conversation_id}/messages",
                            json={"content": question},
                        )
                    events = parse_events(response.text)
                    item["events"] = events
                    item["answer"] = "".join(
                        e["data"].get("content", "") for e in events if e["event"] == "delta"
                    )
                    start = next(e["data"] for e in events if e["event"] == "start")
                    item["run_id"] = start["run_id"]
                    assert start["execution_mode"] == "react"
                    assert start["rag_search_mode"] is None
                    assert not any(e["event"] == "error" for e in events), "Run returned an error"
                    assert any(e["event"] == "done" for e in events), "No final result"
                    tools = [
                        e["data"]
                        for e in events
                        if e["event"] == "agent_status" and e["data"]["status"] == "tool_completed"
                    ]
                    names = [t["tool"]["name"] for t in tools]
                    if case_id in {"R01", "R02", "R05"}:
                        assert not names, "Unexpected tool execution"
                    if case_id == "R01":
                        assert item["answer"].strip() == "AGENT_REACT_OK"
                    elif case_id == "R03":
                        assert names == ["demo__count_text", "demo__calculate"]
                        assert tools[0]["step"] < tools[1]["step"], "Dependent actions ran together"
                        assert "119" in item["answer"]
                    elif case_id == "R04":
                        assert names == ["search_web"] and tools[0]["success"]
                        assert "python.org" in item["answer"] and "2025" in item["answer"]
                        # A direct-return shortcut would have no model evaluation after the tool.
                        index = next(
                            i
                            for i, e in enumerate(events)
                            if e["event"] == "agent_status"
                            and e["data"]["status"] == "tool_completed"
                        )
                        assert any(
                            e["event"] == "agent_status"
                            and e["data"]["status"] == "model_completed"
                            for e in events[index + 1 :]
                        )
                    elif case_id == "R05":
                        assert "2025" in item["answer"]
                    history = (
                        await api(
                            client,
                            "GET",
                            f"/api/conversations/{conversation_id}/messages",
                        )
                    ).json()
                    assert any(
                        m["role"] == "assistant" and m["content"] == item["answer"] for m in history
                    )
                    runs = (
                        await api(
                            client,
                            "GET",
                            f"/api/conversations/{conversation_id}/runs",
                        )
                    ).json()
                    timeline = next(r for r in runs if r["id"] == item["run_id"])
                    assert timeline["status"] == "completed" and timeline["rag_search_mode"] is None
                    item["timeline"] = timeline
                    async with SessionFactory() as session:
                        traces = await session.scalar(
                            select(func.count())
                            .select_from(
                                RoutingTrace,
                            )
                            .where(
                                RoutingTrace.agent_run_id == UUID(item["run_id"]),
                                RoutingTrace.user_id == UUID(report["test_user_id"]),
                            )
                        )
                        assert traces == 0, "ReAct unexpectedly required a routing decision"
                    item["passed"] = True
                except Exception as exc:
                    # Never serialize HTTP exceptions, SDK bodies, cookies or settings.
                    item["passed"] = False
                    item["failure_type"] = type(exc).__name__
                    if isinstance(exc, AssertionError):
                        item["check"] = str(exc)[:200]
                item["seconds"] = round(perf_counter() - started, 3)
                save()
                print(
                    json.dumps(
                        {
                            k: item.get(k)
                            for k in (
                                "id",
                                "passed",
                                "run_id",
                                "seconds",
                                "failure_type",
                                "check",
                            )
                        }
                    ),
                    flush=True,
                )
                if not item["passed"]:
                    break  # Do not spend on follow-up cases after an unresolved failure.
    report["passed"] = len(report["cases"]) == len(CASES) and all(
        c["passed"] for c in report["cases"]
    )
    save()
    print(json.dumps({"passed": report["passed"], "report": str(path)}), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Call real paid APIs and create fixtures"
    )
    args = parser.parse_args()
    if args.live:
        asyncio.run(run_live())
    else:
        print(
            json.dumps(
                [{"id": key, "question": value} for key, value in CASES],
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
