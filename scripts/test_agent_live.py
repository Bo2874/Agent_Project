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

DOCUMENT_CASES = (
    (
        "D01",
        "Theo bảng 3-2, so sánh ANNOY và HNSW về bộ nhớ, cập nhật gia tăng "
        "và tình huống sử dụng. Chỉ dựa trên tài liệu đã chọn.",
    ),
    (
        "D02",
        "Vì sao loại thứ hai phù hợp hơn khi cần cập nhật gia tăng? "
        "Chỉ dựa trên tài liệu, hãy tìm lại bằng chứng cho câu hỏi nối tiếp này.",
    ),
    (
        "D03",
        "Theo tài liệu, HNSW và ANNOY khác nhau thế nào về cập nhật gia tăng? "
        "Sau đó tìm tài liệu chính thức hnswlib trên GitHub để đối chiếu khả năng thêm phần tử. "
        "Nêu rõ phần từ sách và phần từ web.",
    ),
    (
        "D04",
        "Tài liệu giới thiệu những chiến lược chunking nào? "
        "Nêu ưu và nhược điểm của từng loại, chỉ theo nội dung tài liệu.",
    ),
    (
        "D05",
        "Tóm tắt toàn bộ file đã chọn bằng luồng tóm tắt toàn văn. "
        "Nếu vượt giới hạn, chỉ thông báo giới hạn đó; không thay bằng tìm Top-k rồi tóm tắt.",
    ),
    (
        "D06",
        "Theo tài liệu đã chọn, số tài khoản ngân hàng cá nhân của tác giả là gì? "
        "Nếu các đoạn tìm được không nêu, hãy nói không có đủ thông tin, không suy đoán.",
    ),
)


async def run_documents_live(owner_email, document_id, only_case=None):
    import httpx
    from sqlalchemy import func, select

    from backend.core.config import PROJECT_ROOT, get_settings
    from backend.core.security import create_access_token
    from backend.database import SessionFactory
    from backend.main import app
    from backend.models import AgentRun, Document, DocumentVersion, RoutingTrace, User

    if not owner_email or not document_id:
        raise SystemExit("Documents suite requires --owner-email and --document-id.")
    settings = get_settings()
    if not settings.agent_react_enabled or not settings.v2_features.is_enabled("rag"):
        raise SystemExit("Enable AGENT_REACT_ENABLED and V2 RAG before this suite.")
    async with SessionFactory() as session:
        owner = await session.scalar(select(User).where(User.email == owner_email, User.is_active))
        document = (
            await session.scalar(
                select(Document).where(
                    Document.id == UUID(document_id),
                    Document.owner_user_id == owner.id,
                    Document.deleted_at.is_(None),
                    Document.status == "ready",
                )
            )
            if owner
            else None
        )
        if document is None:
            raise SystemExit("Ready document not found for this owner.")
        version = await session.get(DocumentVersion, document.active_version_id)
        owner_id, role = owner.id, owner.role
        title, chunks, pages = document.title, version.chunk_count, version.page_count
    folder = (
        PROJECT_ROOT
        / "data/agent-testing"
        / (datetime.now(UTC).strftime("%Y%m%d-%H%M%S-documents-") + uuid4().hex[:8])
    )
    folder.mkdir(parents=True)
    report = {
        "suite": "react-stage-2",
        "owner": owner_email,
        "document": {"id": document_id, "title": title, "chunks": chunks, "pages": pages},
        "transport": "Real cookie auth, FastAPI ASGI/SSE, DB and inference; no browser",
        "cases": [],
        "passed": False,
    }
    path = folder / "REPORT.json"

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def api(client, method, url, **kwargs):
        response = await client.request(method, url, **kwargs)
        if not response.is_success:
            raise RuntimeError(f"HTTP {response.status_code}")
        return response

    selected_cases = [(k, q) for k, q in DOCUMENT_CASES if only_case is None or k == only_case]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://agent-live.local",
            trust_env=False,
            timeout=720,
        ) as client:
            # A server-minted cookie authenticates the authorized existing document owner.
            # No password change, auth override, credential printing or existing-chat mutation.
            client.cookies.set("access_token", create_access_token(owner_id, role))
            conversation_id = None
            for case_id, question in selected_cases:
                if case_id != "D02" or conversation_id is None:
                    conversation_id = (
                        await api(
                            client,
                            "POST",
                            "/api/conversations",
                            json={
                                "title": f"ReAct PDF acceptance {case_id}",
                            },
                        )
                    ).json()["id"]
                    await api(
                        client,
                        "PUT",
                        f"/api/conversations/{conversation_id}/documents",
                        json={"document_ids": [document_id]},
                    )
                if case_id == "D02" and only_case == "D02":
                    question = "Theo bảng 3-2, vì sao HNSW phù hợp khi cần cập nhật gia tăng?"
                item = {"id": case_id, "question": question, "conversation_id": conversation_id}
                report["cases"].append(item)
                started = perf_counter()
                try:
                    async with asyncio.timeout(720):
                        response = await api(
                            client,
                            "POST",
                            f"/api/conversations/{conversation_id}/messages",
                            json={
                                "content": question,
                                "rag_search_mode": "deep" if case_id == "D04" else "fast",
                            },
                        )
                    events = parse_events(response.text)
                    item["events"] = events
                    item["answer"] = "".join(
                        e["data"].get("content", "") for e in events if e["event"] == "delta"
                    )
                    start = next(e["data"] for e in events if e["event"] == "start")
                    item["run_id"] = start["run_id"]
                    assert start["execution_mode"] == "react"
                    assert not any(e["event"] == "error" for e in events), "Run failed"
                    done = next(e["data"] for e in events if e["event"] == "done")
                    refs = done["citations"]
                    assert all(r["document_id"] == document_id for r in refs)
                    tools = [
                        e["data"]
                        for e in events
                        if e["event"] == "agent_status" and e["data"]["status"] == "tool_completed"
                    ]
                    names = [t["tool"]["name"] for t in tools]
                    if case_id == "D05":
                        assert names == ["summarize_documents"] and not tools[0]["success"]
                        assert not refs and any(
                            w in item["answer"].lower() for w in ("giới hạn", "vượt", "phạm vi")
                        )
                    else:
                        assert (
                            1 <= names.count("search_documents") <= (2 if case_id == "D03" else 1)
                        )
                        assert refs and len(refs) <= 10 and all(r["page_start"] for r in refs)
                        if case_id == "D03":
                            assert names.count("search_web") == 1
                            assert "github.com" in item["answer"]
                        else:
                            assert names == ["search_documents"]
                    if case_id == "D01":
                        assert (
                            "ANNOY" in item["answer"].upper() and "HNSW" in item["answer"].upper()
                        )
                    if case_id == "D06":
                        assert "không" in item["answer"].lower()
                    history = (
                        await api(client, "GET", f"/api/conversations/{conversation_id}/messages")
                    ).json()
                    stored = next(m for m in history if m["id"] == done["message_id"])
                    assert stored["content"] == item["answer"]
                    assert {r["chunk_id"] for r in stored["citations"]} == {
                        r["chunk_id"] for r in refs
                    }
                    item["source_checks"] = []
                    for ref in refs:
                        url = (
                            f"/api/conversations/{conversation_id}/messages/"
                            f"{done['message_id']}/citations/{ref['citation_id']}"
                        )
                        source = (await api(client, "GET", url)).json()
                        assert source["chunk_id"] == ref["chunk_id"] and source["text"].strip()
                        item["source_checks"].append(source)
                    async with SessionFactory() as session:
                        run = await session.get(AgentRun, UUID(item["run_id"]))
                        assert run.status == "completed"
                        item["trace"] = run.rag_trace
                        traces = await session.scalar(
                            select(func.count())
                            .select_from(RoutingTrace)
                            .where(
                                RoutingTrace.agent_run_id == run.id,
                                RoutingTrace.user_id == owner_id,
                            )
                        )
                        assert traces == 0
                        actions = run.rag_trace["document_actions"]
                        if case_id == "D05":
                            assert actions[-1]["error_code"] == "document_summary_limit"
                        else:
                            search = next(a for a in actions if a["tool"] == "search_documents")
                            assert search["query_origin"] == "agent_standalone_no_extra_rewrite"
                            if case_id == "D02":
                                assert "hnsw" in search["query"].lower()
                            if case_id == "D04":
                                assert search["search_mode"] == "deep"
                                assert 0 < search["retrieval"]["rerank_pair_count"] <= 10
                    item["passed"] = True
                except Exception as exc:
                    item.update(passed=False, failure_type=type(exc).__name__)
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
                    break
            # Isolation is asserted at API boundaries, not by asking an LLM to refuse.
            if report["cases"] and report["cases"][-1]["passed"]:
                last = report["cases"][-1]
                client.cookies.clear()
                await api(
                    client,
                    "POST",
                    "/api/auth/register",
                    json={
                        "email": f"pdf-isolation-{uuid4().hex[:10]}@example.com",
                        "password": secrets.token_urlsafe(24),
                        "full_name": "PDF isolation fixture",
                    },
                )
                own = (await api(client, "POST", "/api/conversations", json={})).json()["id"]
                denied = await client.put(
                    f"/api/conversations/{own}/documents", json={"document_ids": [document_id]}
                )
                assert denied.status_code in {403, 404}
                denied = await client.get(f"/api/conversations/{last['conversation_id']}/messages")
                assert denied.status_code in {403, 404}
                source_case = next(
                    (c for c in reversed(report["cases"]) if c.get("source_checks")), None
                )
                if source_case:
                    completed = next(
                        e["data"] for e in source_case["events"] if e["event"] == "done"
                    )
                    ref = source_case["source_checks"][0]
                    denied = await client.get(
                        f"/api/conversations/{source_case['conversation_id']}/messages/"
                        f"{completed['message_id']}/citations/{ref['citation_id']}"
                    )
                    assert denied.status_code == 404
                report["cross_user_denied"] = True
    report["passed"] = len(report["cases"]) == len(selected_cases) and all(
        c["passed"] for c in report["cases"]
    )
    save()
    print(json.dumps({"passed": report["passed"], "report": str(path)}), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


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
    parser.add_argument("--suite", choices=("react", "documents"), default="react")
    parser.add_argument("--owner-email")
    parser.add_argument("--document-id")
    parser.add_argument("--case", choices=tuple(k for k, _ in DOCUMENT_CASES))
    args = parser.parse_args()
    if args.live:
        if args.suite == "documents":
            asyncio.run(run_documents_live(args.owner_email, args.document_id, args.case))
        else:
            asyncio.run(run_live())
    else:
        print(
            json.dumps(
                [
                    {"id": key, "question": value}
                    for key, value in (DOCUMENT_CASES if args.suite == "documents" else CASES)
                ],
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
