"""Offline Google acceptance: actual queue, graph, approvals, journal and HTTP adapters.

Only Google and web responses / root model output are deterministic fixtures.
No Google account, search provider or paid model is contacted.
"""

import asyncio
import json
import os
from contextlib import suppress
from unittest.mock import patch
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import make_url

from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.tools import ToolRegistry, ToolResult, create_context_tool
from agent_core.tools.google import register_google_tools
from agent_core.tools.web_search import SearchWebInput
from backend.models import AgentApproval, AgentRun, RunStep, ToolCall
from backend.services.google.tools import GoogleToolService
from scripts.artifact_fixture import OfflineArtifactRuntime


class OfflineGoogleModel:
    provider, model, max_tokens = "fixture", "google-offline", 200

    def __init__(self, ref, scenario):
        self.ref, self.scenario = ref, scenario

    async def aclose(self):
        pass

    async def stream(self, messages, tools=None, **kwargs):
        observations = {}
        for message in messages:
            if message.get("role") == "tool":
                value = json.loads(message["content"])
                observations[message["tool_call_id"]] = value
        name, args, identity = None, None, None
        if tools:
            if self.scenario == "research" and "fixture-search" not in observations:
                name, args, identity = (
                    "search_web",
                    {"query": "offline AI repository fixture"},
                    "search",
                )
            elif "fixture-read" not in observations:
                name, args, identity = (
                    "google_sheets_read",
                    {"ref": self.ref, "range_a1": "Sheet1!A1:B100"},
                    "read",
                )
            elif self.scenario != "read" and "fixture-prepare" not in observations:
                rows = [["https://fixture.example/agent", "Offline research"]]
                if self.scenario == "research":
                    source = observations["fixture-search"]["data"]["sources"][0]["url"]
                    rows[0][0] = source
                else:
                    rows = [["Updated by approved fixture"]]
                name, args, identity = (
                    "google_sheets_prepare",
                    {
                        "ref": self.ref,
                        "change": {
                            "kind": "append_rows"
                            if self.scenario == "research"
                            else "update_range",
                            "range_a1": "Sheet1!A1:B100"
                            if self.scenario == "research"
                            else "Sheet1!B2:B2",
                            "rows": rows,
                        },
                    },
                    "prepare",
                )
            elif self.scenario != "read" and "fixture-commit" not in observations:
                proposal = observations["fixture-prepare"]["data"]
                name, args, identity = (
                    "google_sheets_append"
                    if self.scenario == "research"
                    else "google_sheets_update",
                    proposal["commit_arguments"],
                    "commit",
                )
        usage = TokenUsage(prompt_tokens=30, completion_tokens=20, total_tokens=50)
        if name:
            yield LLMChunk(
                tool_call_deltas=(
                    LLMToolCallDelta(
                        index=0, id="fixture-" + identity, name=name, arguments=json.dumps(args)
                    ),
                ),
                finish_reason="tool_calls",
                usage=usage,
            )
        else:
            yield LLMChunk(
                content="Completed offline Google scenario.", finish_reason="stop", usage=usage
            )


async def run_google_scenario(db, scenario):
    from urllib.parse import parse_qs

    import httpx

    from agent_core.tools import ToolContext
    from backend.schemas.google import GoogleFileRef
    from backend.services import agent_context
    from backend.services.google.access import GoogleAccess
    from backend.services.google.oauth import GoogleOAuth

    oauth = GoogleOAuth(db.settings, transport=db.transport)
    async with db.sessions() as session:
        start = await oauth.begin(session, db.owner)
        state = parse_qs(httpx.URL(start.authorization_url).query.decode())["state"][0]
        connection = await oauth.complete(session, db.owner, state=state, code="fixture-code")
        access = GoogleAccess(oauth)
        ref = GoogleFileRef(
            connection_id=connection.id, file_id="sheet-a", context_id=db.conversation
        )
        await access.set_files(session, db.owner, connection.id, [ref.file_id])
        await access.set_work_files(session, db.owner, db.conversation, [ref])
    service = GoogleToolService(access=access, sessions=db.sessions)
    registry = ToolRegistry()
    register_google_tools(registry, service)
    search_calls = []

    async def search(context, args):
        search_calls.append(args["query"])
        return ToolResult.success(
            "Offline source fixture.",
            data={
                "sources": [{"url": "https://fixture.example/agent", "title": "Fixture"}],
                "search_performed": True,
                "search_requests": 1,
            },
            usage=TokenUsage(),
        )

    registry.register(
        create_context_tool(
            name="search_web",
            description="Offline fixture web search",
            args_schema=SearchWebInput,
            coroutine=search,
        )
    )
    url = make_url(os.environ["GOOGLE_TEST_DATABASE_URL"])
    config = db.settings.model_copy(
        update=dict(
            db_host=url.host,
            db_port=url.port,
            db_name=url.database,
            db_user=url.username,
            db_password=SecretStr(url.password),
            memory_extraction_enabled=False,
            agent_react_enabled=True,
            background_queue_name="google-" + uuid4().hex[:12],
        )
    )
    runtime = await OfflineArtifactRuntime(
        config,
        db.sessions,
        None,
        model_factory=lambda: OfflineGoogleModel(ref.model_dump(mode="json"), scenario),
        tool_registry=registry,
    ).start()
    worker = None
    try:
        runtime.patches.enter_context(
            patch.object(agent_context, "google_tool_service", lambda: service)
        )
        run_id = await runtime.enqueue(db.owner, db.conversation, "Google " + scenario)
        worker = asyncio.create_task(runtime.worker.run_once())
        approvals = []
        async with asyncio.timeout(35):
            while not worker.done():
                async with db.sessions() as session:
                    pending = (
                        await session.scalars(
                            select(AgentApproval).where(
                                AgentApproval.agent_run_id == run_id,
                                AgentApproval.decision.is_(None),
                            )
                        )
                    ).all()
                    for approval in pending:
                        # Deliberate user stand-in only inside this offline acceptance fixture.
                        assert len(db.provider.writes) == 0
                        approval.decision = "approved"
                        approvals.append(str(approval.id))
                    await session.commit()
                await asyncio.sleep(0.05)
            await worker
        async with db.sessions() as session:
            run = await session.get(AgentRun, run_id)
            calls = (
                await session.scalars(
                    select(ToolCall).join(RunStep).where(RunStep.agent_run_id == run_id)
                )
            ).all()
            assert all(call.status == "completed" for call in calls), [
                (call.tool_name, call.status, call.error_code) for call in calls
            ]
            report = {
                "status": run.status,
                "stop_reason": run.stop_reason,
                "error": run.error_message,
                "search_calls": len(search_calls),
                "approvals": len(approvals),
                "writes": len(db.provider.writes),
                "tools": [call.tool_name for call in calls],
            }
        if scenario == "research":
            assert any(
                row[0] == "https://fixture.example/agent" for row in db.provider.sheet_values
            )
        if scenario == "update":
            assert db.provider.sheet_values[1][1] == "Updated by approved fixture"
        # Pinned scope is checked even when an old catalog/result still exists.
        pinned = await service.pin(
            ToolContext(
                user_id=db.owner, conversation_id=db.conversation, user_role="user", run_id=run_id
            )
        )
        await service.guard(pinned, "google_sheets_read")
        return report
    finally:
        if worker is not None and not worker.done():
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await runtime.close()
