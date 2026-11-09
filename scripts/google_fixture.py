"""Offline Google acceptance through Python, real queue/graph/journal and Docker."""

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
from agent_core.tools.web_search import SearchWebInput
from backend.models import AgentApproval, AgentRun, RunStep, ToolCall
from backend.services.google.context import GoogleContextService
from scripts.artifact_fixture import OfflineArtifactRuntime


class OfflineGoogleModel:
    provider, model, max_tokens = "fixture", "google-offline", 200

    def __init__(self, ref, scenario):
        self.ref, self.scenario = ref, scenario

    async def aclose(self):
        pass

    async def stream(self, messages, tools=None, **kwargs):
        observations = {
            m["tool_call_id"]: json.loads(m["content"]) for m in messages if m.get("role") == "tool"
        }
        name, args, identity = None, None, None
        if tools:
            if self.scenario == "research" and "fixture-search" not in observations:
                name, args, identity = (
                    "search_web",
                    {"query": "offline AI repository fixture"},
                    "search",
                )
            elif "fixture-read" not in observations:
                name, args, identity = "run_python_code", self.read_args(), "read"
            elif self.scenario != "read" and "fixture-write" not in observations:
                name, args, identity = "run_python_code", self.write_args(observations), "write"
            elif self.scenario != "read" and "fixture-check" not in observations:
                name, args, identity = "run_python_code", self.read_args(), "check"
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

    def read_args(self):
        return {
            "code": "from pathlib import Path; print(Path('/input/input-0.json').read_text())",
            "google_inputs": [{"ref": self.ref}],
        }

    def write_args(self, observations):
        if self.scenario == "research":
            url = observations["fixture-search"]["data"]["sources"][0]["url"]
            payload = {
                "appendCells": {
                    "sheetId": 0,
                    "rows": [
                        {
                            "values": [
                                {"userEnteredValue": {"stringValue": url}},
                                {"userEnteredValue": {"stringValue": "Offline research"}},
                            ]
                        }
                    ],
                    "fields": "userEnteredValue",
                }
            }
        else:
            payload = {
                "updateCells": {
                    "start": {"sheetId": 0, "rowIndex": 1, "columnIndex": 1},
                    "rows": [
                        {
                            "values": [
                                {"userEnteredValue": {"stringValue": "Updated by Python fixture"}}
                            ]
                        }
                    ],
                    "fields": "userEnteredValue",
                }
            }
        body = json.dumps({"requests": [payload]})
        return {
            "code": "from pathlib import Path; Path('changes.json').write_text(" + repr(body) + ")",
            "google_inputs": [{"ref": self.ref}],
            "outputs": [{"path": "changes.json", "publish": False}],
            "google_actions": [
                {"kind": "batch_update", "input_index": 0, "request_path": "changes.json"}
            ],
        }


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
    import tempfile
    from pathlib import Path

    from agent_core.tools.python import register_python_tool
    from artifact_runtime.app import create_app
    from artifact_runtime.containers import DockerBroker
    from backend.services.artifacts.python import PythonToolService
    from backend.services.artifacts.runner_client import ArtifactRunnerClient
    from backend.services.artifacts.service import ArtifactService
    from backend.services.artifacts.storage import ArtifactStorage
    from backend.services.google.bridge import GooglePythonBridge

    temporary = tempfile.TemporaryDirectory(prefix="google-python-graph-")
    root = Path(temporary.name)
    artifacts = ArtifactService(ArtifactStorage(root / "private"))
    service = GoogleContextService(access=access, sessions=db.sessions)
    broker = create_app(
        DockerBroker(root / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker")),
        key="k" * 32,
    )
    python = PythonToolService(
        service=artifacts,
        sessions=db.sessions,
        publication_enabled=True,
        google_bridge=GooglePythonBridge(
            access, db.sessions, artifacts.storage, context_service=service
        ),
        runner_factory=lambda owner: ArtifactRunnerClient(
            "http://runner",
            key="k" * 32,
            storage=artifacts.storage,
            owner=owner,
            transport=httpx.ASGITransport(app=broker),
        ),
    )
    registry = ToolRegistry()
    register_python_tool(registry, python)
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
            python_sandbox_enabled=True,
            workspace_artifacts_enabled=True,
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
            patch.object(agent_context, "google_context_service", lambda: service)
        )
        run_id = await runtime.enqueue(db.owner, db.conversation, "Google " + scenario)
        worker = asyncio.create_task(runtime.worker.run_once())
        async with asyncio.timeout(150):
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
                "approvals": len(
                    (
                        await session.scalars(
                            select(AgentApproval).where(AgentApproval.agent_run_id == run_id)
                        )
                    ).all()
                ),
                "writes": len(db.provider.writes),
                "tools": [call.tool_name for call in calls],
            }
        if scenario == "research":
            assert any(
                row[0] == "https://fixture.example/agent" for row in db.provider.sheet_values
            )
        if scenario == "update":
            assert db.provider.sheet_values[1][1] == "Updated by Python fixture"
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
        temporary.cleanup()
