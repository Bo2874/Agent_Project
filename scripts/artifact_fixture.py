"""Offline acceptance model with real graph, PostgreSQL queue/checkpoints and artifact tools.

Only use with task-owned test users/databases. No external model, MCP or trace export.
"""

import json
import re
from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from agent_core.context_budget import ContextTokenBudget
from agent_core.graph import AgentLimits
from agent_core.graph.checkpointing import agent_thread_id
from agent_core.llm.types import LLMChunk, LLMToolCallDelta, TokenUsage
from agent_core.skills import SkillRegistry, SkillSelectionConfig
from agent_core.tools import ToolRegistry, ToolSelectionConfig
from agent_core.tools.artifacts import register_artifact_tools
from backend.background.worker import BackgroundJobRegistry, BackgroundWorker
from backend.services.artifacts.tools import ArtifactToolService


class OfflineArtifactModel:
    provider, model, max_tokens = "fixture", "artifact-offline", 200

    async def aclose(self):
        pass

    async def stream(self, messages, tools=None, **kwargs):
        if any(message.get("role") == "tool" for message in messages) or not tools:
            yield LLMChunk(
                content="Saved a verified Excel version.",
                finish_reason="stop",
                usage=TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            )
            return
        query = next(
            message["content"] for message in reversed(messages) if message["role"] == "user"
        )
        match = re.search(r"edit\s+([0-9a-f-]{36})", query)
        operations = [
            {
                "kind": "write_cells",
                "sheet": "Sheet1",
                "start": "A1",
                "values": [["Month", "Revenue"], ["Jan", 20 if match else 10], ["Feb", 30]],
            },
            {"kind": "set_formula", "sheet": "Sheet1", "cell": "B4", "formula": "=SUM(B2:B3)"},
        ]
        if not match:
            operations.extend(
                [
                    {"kind": "add_sheet", "name": "Notes"},
                    {
                        "kind": "add_chart",
                        "sheet": "Sheet1",
                        "chart_type": "column",
                        "data_range": "B1:B3",
                        "categories_range": "A2:A3",
                        "title": "Revenue",
                    },
                ]
            )
        args = {"name": "fixture-report.xlsx", "payload": {"operations": operations}}
        if match:
            args["parent_version_id"] = match.group(1)
        yield LLMChunk(
            tool_call_deltas=(
                LLMToolCallDelta(
                    index=0,
                    id="artifact-fixture-1",
                    name="artifact_edit" if match else "artifact_create",
                    arguments=json.dumps(args),
                ),
            ),
            finish_reason="tool_calls",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        )


class OfflineArtifactRuntime:
    def __init__(self, settings, sessions, service, *, runner_factory=None):
        self.settings, self.sessions, self.service = settings, sessions, service
        self.registry = ToolRegistry()
        register_artifact_tools(
            self.registry,
            ArtifactToolService(service=service, sessions=sessions, runner_factory=runner_factory),
        )
        self.patches, self.run_ids = ExitStack(), []

    async def start(self):
        try:
            return await self._start()
        except BaseException:
            if hasattr(self, "checkpoints"):
                await self.checkpoints.close()
            self.patches.close()
            raise

    async def _start(self):
        from backend import dependencies_llm, dependencies_skills, dependencies_tools
        from backend.background import queue
        from backend.routes import chat
        from backend.services import (
            agent_context,
            agent_jobs,
            agent_runs,
            langgraph_checkpoints,
            langsmith_tracing,
            memory,
            run_events,
            usage_limits,
        )

        for module in (
            queue,
            chat,
            agent_context,
            agent_jobs,
            agent_runs,
            memory,
            run_events,
            usage_limits,
        ):
            self.patches.enter_context(patch.object(module, "SessionFactory", self.sessions))
            if hasattr(module, "get_settings"):
                self.patches.enter_context(
                    patch.object(module, "get_settings", lambda: self.settings)
                )
        self.checkpoints = langgraph_checkpoints.LangGraphCheckpointService(
            conninfo=self.settings.langgraph_checkpoint_database_uri, min_size=1, max_size=4
        )
        await self.checkpoints.start()
        for module in (chat, langgraph_checkpoints):
            self.patches.enter_context(
                patch.object(module, "get_langgraph_checkpointer", lambda: self.checkpoints.saver)
            )
        self.patches.enter_context(
            patch.object(dependencies_llm, "create_chat_model", lambda **_: OfflineArtifactModel())
        )
        self.patches.enter_context(
            patch.object(dependencies_tools, "get_tool_registry", lambda: self.registry)
        )
        self.patches.enter_context(
            patch.object(dependencies_skills, "get_skill_registry", lambda: SkillRegistry())
        )
        tracing = langsmith_tracing.LangSmithTracingService(
            enabled=False,
            api_key="",
            endpoint="",
            project="offline",
            sampling_rate=0,
            hash_salt="fixture",
            app_env="test",
        )
        self.patches.enter_context(patch.object(chat, "get_langsmith_tracing", lambda: tracing))
        registry = BackgroundJobRegistry()
        registry.register("agent.run", agent_jobs.agent_run_handler)
        self.worker = BackgroundWorker(
            queue=queue.PostgresJobQueue(),
            registry=registry,
            queue_name=self.settings.background_queue_name,
            worker_id=f"artifact-fixture-{uuid4()}",
            lease_seconds=30,
            control_poll_seconds=0.1,
        )
        return self

    async def enqueue(self, owner, conversation_id, query):
        from backend.models import AgentRun, Message
        from backend.services.agent_jobs import enqueue_run

        async with self.sessions() as session:
            message = Message(
                id=uuid4(), conversation_id=conversation_id, role="user", content=query
            )
            session.add(message)
            await session.flush()
            run = AgentRun(
                id=uuid4(),
                conversation_id=conversation_id,
                user_message_id=message.id,
                status="running",
            )
            session.add(run)
            await session.commit()
        self.run_ids.append(run.id)
        await enqueue_run(
            dict(
                agent_run_id=run.id,
                conversation_id=conversation_id,
                user_message_id=message.id,
                user_id=owner,
                model=OfflineArtifactModel(),
                user_message_content=query,
                user_message_observed_at=datetime.now(UTC),
                model_messages=[{"role": "user", "content": query}],
                limits=AgentLimits(max_total_tokens=20000),
                context_budget=ContextTokenBudget(),
                tool_selection_config=ToolSelectionConfig(
                    lazy_load_threshold=100, max_loaded_tools=4, schema_token_budget=12000
                ),
                skill_selection_config=SkillSelectionConfig(),
                history_summary_context=(),
                rag_search_mode="fast",
            )
        )
        return run.id

    async def close(self):
        for run_id in self.run_ids:
            await self.checkpoints.saver.adelete_thread(agent_thread_id(run_id))
        await self.checkpoints.close()
        self.patches.close()
