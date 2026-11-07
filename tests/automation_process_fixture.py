"""Test-only subprocess: isolated schema, production queue/checkpointer, fake providers."""

import asyncio
import json
import os
import re
import sys
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = make_url(os.environ["GOOGLE_TEST_DATABASE_URL"])
    schema = os.environ["AUTOMATION_QA_SCHEMA"]
    state_path = Path(os.environ["AUTOMATION_QA_STATE"])
    if not url.database.endswith("_test") or not re.fullmatch(
        r"test_workspace_[a-f0-9]{32}", schema
    ):
        raise RuntimeError("Requires isolated test database/schema")
    import backend.core.config as config

    settings = config.Settings(
        _env_file=None,
        db_password="fixture",
        auth_secret_key="process-fixture-" * 3,
        automations_enabled=True,
        agent_react_enabled=True,
        memory_extraction_enabled=False,
        google_connector_enabled=True,
        google_client_id="fixture-client.apps.googleusercontent.com",
        google_client_secret="client-secret-canary",
        google_credential_encryption_keys=os.environ["AUTOMATION_QA_KEY"],
        deepseek_model="automation-process-fixture",
        deepseek_max_tokens=128,
        agent_max_total_tokens=128,
    )
    config.get_settings = lambda: settings
    import backend.database as database

    database.engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    database.SessionFactory = async_sessionmaker(database.engine, expire_on_commit=False)

    async def run():
        if sys.argv[1] == "scheduler":
            from datetime import UTC, datetime

            from backend.services.automations.dispatch import dispatch_due

            async with database.SessionFactory() as session:
                print(await dispatch_due(session, now=datetime.now(UTC)), flush=True)
            return
        import httpx
        from google_fixtures import FakeGoogle

        import backend.dependencies_llm as llm
        import backend.dependencies_skills as skills
        import backend.dependencies_tools as tools
        from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
        from agent_core.skills import SkillRegistry
        from agent_core.tools import ToolRegistry, ToolResult, create_context_tool
        from agent_core.tools.google import register_google_tools
        from backend.services import agent_context, langgraph_checkpoints
        from backend.services.google.access import GoogleAccess
        from backend.services.google.credentials import GoogleCredentials
        from backend.services.google.tools import GoogleToolService

        provider = FakeGoogle()

        def load_state():
            return json.loads(state_path.read_text(encoding="utf-8"))

        def save_state(state):
            temp = state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(state), encoding="utf-8")
            temp.replace(state_path)

        async def handle(request):
            state = load_state()
            provider.writes = state.get("writes", [])
            provider.sheet_values = state.get("sheet_values", provider.sheet_values)
            provider.lose_write_response = state.get("lose_response", False)
            try:
                return await provider.handle(request)
            finally:
                state.update(writes=provider.writes, sheet_values=provider.sheet_values)
                save_state(state)

        service = GoogleToolService(
            access=GoogleAccess(GoogleCredentials(settings, transport=httpx.MockTransport(handle))),
            sessions=database.SessionFactory,
        )
        agent_context.google_tool_service = lambda: service

        async def search(context, arguments):
            return ToolResult.success(
                "Fixture repository",
                data={
                    "rows": [["https://new.example/repo", "Fixture"]],
                    "usage": {"total_tokens": 10_000},
                },
                usage=TokenUsage(total_tokens=10_000),
            )

        def registry():
            result = ToolRegistry()
            register_google_tools(result, service)
            result.register(
                create_context_tool(
                    name="search_web",
                    description="Search fixture",
                    coroutine=search,
                    args_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                )
            )
            return result

        class Model:
            provider, model, max_tokens = "fixture", settings.deepseek_model, 128

            async def stream(self, messages, **kwargs):
                observations = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
                if not observations:
                    name, args = "search_web", {"query": "AI repositories"}
                elif len(observations) == 1:
                    refs = []
                    decoder = json.JSONDecoder()
                    for message in messages:
                        content = message.get("content") or ""
                        for match in re.finditer(r"\[", content):
                            try:
                                value, _ = decoder.raw_decode(content[match.start() :])
                            except ValueError:
                                continue
                            if isinstance(value, list):
                                for block in value:
                                    if (
                                        isinstance(block, dict)
                                        and block.get("source") == "selected_google_files"
                                    ):
                                        refs = json.loads(block["content"])
                            if (
                                isinstance(value, list)
                                and value
                                and isinstance(value[0], dict)
                                and "connection_id" in value[0]
                                and "file_id" in value[0]
                            ):
                                refs = value
                    assert refs, "Model must receive selected file context"
                    ref = {k: refs[0][k] for k in ("connection_id", "file_id", "context_id")}
                    name, args = (
                        "google_sheets_prepare",
                        {
                            "ref": ref,
                            "change": {
                                "kind": "append_rows",
                                "range_a1": "Sheet1!A1:B100",
                                "rows": observations[0]["data"]["rows"],
                                "dedup_column": 0,
                            },
                        },
                    )
                elif len(observations) == 2:
                    name, args = (
                        "google_sheets_append",
                        observations[-1]["data"]["commit_arguments"],
                    )
                else:
                    name, args = None, {}
                state = load_state()
                state.setdefault("model_calls", []).append(name or "final")
                save_state(state)
                if name:
                    yield LLMChunk(
                        finish_reason="tool_calls",
                        usage=TokenUsage(total_tokens=10_000),
                        tool_call_deltas=(
                            LLMToolCallDelta(
                                index=0,
                                id=f"call-{len(observations)}",
                                name=name,
                                arguments=json.dumps(args),
                            ),
                        ),
                    )
                else:
                    yield LLMChunk(
                        content="Scheduled report complete",
                        finish_reason="stop",
                        usage=TokenUsage(total_tokens=10_000),
                    )

            async def aclose(self):
                pass

        llm.create_chat_model = lambda **kwargs: Model()
        tools.get_tool_registry = registry
        skills.get_skill_registry = SkillRegistry
        from psycopg.conninfo import make_conninfo

        checkpoints = langgraph_checkpoints.LangGraphCheckpointService(
            conninfo=make_conninfo(
                host=url.host,
                port=url.port,
                dbname=url.database,
                user=url.username,
                password=url.password,
                options=f"-c search_path={schema},public",
            ),
            min_size=1,
            max_size=2,
            setup_schema=sys.argv[1] == "setup",
        )
        await checkpoints.start()
        langgraph_checkpoints.get_langgraph_checkpointer = lambda: checkpoints.saver
        try:
            if sys.argv[1] == "setup":
                return
            from backend.services.run_events import OperationJournal

            original = OperationJournal.finish

            async def finish(journal, key, result, tokens):
                await original(journal, key, result, tokens)
                if key.endswith(".call-2") and os.environ.get("AUTOMATION_QA_CRASH") == "1":
                    os._exit(91)  # Deliberate death of this test child after committed receipt.

            OperationJournal.finish = finish
            from backend.services.agent_runs import AgentRunRecorder

            record_tool = AgentRunRecorder._record_tool_completed

            async def crash_after_record(recorder, event):
                await record_tool(recorder, event)
                if (
                    event.tool_call.name == "search_web"
                    and os.environ.get("AUTOMATION_QA_CRASH") == "search-record"
                ):
                    os._exit(92)  # Completed paid tool, checkpoint not advanced yet.

            AgentRunRecorder._record_tool_completed = crash_after_record
            from backend.background.handlers import build_background_job_registry
            from backend.background.queue import PostgresJobQueue
            from backend.background.worker import BackgroundWorker
            from backend.services.langsmith_tracing import (
                close_langsmith_tracing,
                start_langsmith_tracing,
            )

            start_langsmith_tracing()
            try:
                worker = BackgroundWorker(
                    queue=PostgresJobQueue(),
                    registry=build_background_job_registry(),
                    queue_name="default",
                    worker_id=f"process-{os.getpid()}",
                    lease_seconds=30,
                    control_poll_seconds=0.1,
                )
                await worker.run_once()
            finally:
                await close_langsmith_tracing()
        finally:
            await checkpoints.close()

    async def lifecycle():
        try:
            await run()
        finally:
            await database.engine.dispose()

    asyncio.run(lifecycle())


if __name__ == "__main__":
    main()
