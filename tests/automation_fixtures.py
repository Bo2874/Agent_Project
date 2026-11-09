"""Real PostgreSQL queue/runtime; only model/network providers are replaced."""

import importlib
import sys

from langgraph.checkpoint.memory import InMemorySaver


def runtime(db, monkeypatch, model):
    # Load aliases before replacing the database factory, so undo cannot leave a
    # lazily imported module pointing at a schema another test has dropped.
    for name in (
        "backend.background.handlers",
        "backend.routes.chat",
        "backend.services.agent_jobs",
        "backend.services.agent_runs",
        "backend.services.agent_context",
        "backend.services.run_events",
        "backend.services.memory",
        "backend.services.usage_limits",
        "backend.services.tool_approvals",
        "backend.services.document_tools",
        "backend.services.document_qa",
        "backend.services.history_compression",
        "backend.services.plugin_runtime",
        "backend.services.plugin_credentials",
        "backend.services.google.context",
        "backend.services.artifacts.tools",
        "backend.services.routing",
        "backend.services.automations.execution",
    ):
        importlib.import_module(name)
    import backend.database as database
    import backend.dependencies_llm as llm
    import backend.dependencies_skills as skills
    import backend.dependencies_tools as tools
    from agent_core.skills import SkillRegistry
    from agent_core.tools import ToolRegistry
    from backend.core.config import Settings
    from backend.services import langgraph_checkpoints, langsmith_tracing
    from backend.services.automations import execution

    settings = Settings(
        _env_file=None,
        db_password="fixture",
        auth_secret_key="a" * 32,
        automations_enabled=True,
        agent_react_enabled=True,
        memory_extraction_enabled=False,
        deepseek_model=model.model,
    )
    original = database.SessionFactory
    for module in list(sys.modules.values()):
        if module is not None and getattr(module, "__name__", "").startswith("backend."):
            if getattr(module, "SessionFactory", None) is original:
                monkeypatch.setattr(module, "SessionFactory", db.sessions)
            if hasattr(module, "get_settings"):
                monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(database, "SessionFactory", db.sessions)
    monkeypatch.setattr(execution, "SessionFactory", db.sessions, raising=False)
    monkeypatch.setattr(llm, "create_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(tools, "get_tool_registry", ToolRegistry)
    monkeypatch.setattr(skills, "get_skill_registry", SkillRegistry)
    saver = InMemorySaver()
    monkeypatch.setattr(langgraph_checkpoints, "get_langgraph_checkpointer", lambda: saver)
    monkeypatch.setattr(langsmith_tracing, "_tracing_service", None)
    langsmith_tracing.start_langsmith_tracing()
    return settings, saver


async def queued(db, *, trigger="manual", body=None):
    from test_automation_api import draft

    from backend.models.automation import Automation
    from backend.schemas.automation import AutomationDraft
    from backend.services.automations.dispatch import enqueue_automation_run
    from backend.services.automations.service import create_automation, current_revision

    async with db.sessions() as session:
        view = await create_automation(
            session, db.owner, AutomationDraft.model_validate(body or draft())
        )
        auto = await session.get(Automation, view.id)
        row = await enqueue_automation_run(
            session,
            auto,
            await current_revision(session, auto),
            trigger=trigger,
            idempotency_key="manual" if trigger == "manual" else None,
        )
        await session.commit()
        return view, row.id
