"""Offline PostgreSQL check; exclusively for an explicitly configured scratch database."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text

from agent_core.routing.policy import DecisionPolicy
from agent_core.routing.types import (
    DecisionOption,
    DecisionRequest,
    DecisionResult,
    DecisionTelemetry,
)
from backend.core.config import PROJECT_ROOT, get_settings
from backend.database import SessionFactory, engine
from backend.models import AgentRun, Conversation, RoutingTrace, User
from backend.services.routing import BackendDecisionProvider, routing_metrics


async def check_database():
    user_id, other_id, conversation_id, run_id = [uuid4() for _ in range(4)]
    async with SessionFactory() as session:
        session.add_all(
            [
                User(id=user_id, email=f"{user_id}@example.test", password_hash="synthetic"),
                User(id=other_id, email=f"{other_id}@example.test", password_hash="synthetic"),
            ]
        )
        await session.flush()
        session.add(Conversation(id=conversation_id, user_id=user_id))
        await session.flush()
        session.add(AgentRun(id=run_id, conversation_id=conversation_id))
        await session.commit()

    class Provider:
        async def decide(self, request):
            assert "private-result-marker" not in request.state
            assert "owner@example.test" not in request.state
            return DecisionResult(
                selected_id="calculator",
                probabilities={"calculator": 0.98, "none": 0.02},
                telemetry=DecisionTelemetry(
                    provider="jev",
                    model_version="fixture-model-v1",
                    input_tokens=21,
                    output_tokens=0,
                ),
            )

    request = DecisionRequest(
        state=json.dumps(
            {
                "goal": "Calculate 25 times 17 owner@example.test",
                "tool_body": "private-result-marker",
            }
        ),
        instructions="Choose one option",
        options=(
            DecisionOption(id="calculator", description="Calculate"),
            DecisionOption(id="none", description="No tool"),
        ),
    )
    tokens = []

    async def record_tokens(count):
        tokens.append(count)

    first = BackendDecisionProvider(Provider(), policy=DecisionPolicy(max_calls=1))
    await first.bind_run(user_id=user_id, run_id=run_id, token_recorder=record_tokens)
    assert (await first.decide(request)).selected_id == "calculator"
    resumed = BackendDecisionProvider(Provider(), policy=DecisionPolicy(max_calls=1))
    await resumed.bind_run(user_id=user_id, run_id=run_id)
    assert resumed.calls == 1 and resumed.reserved_tokens > 0
    assert (await resumed.decide(request)).selected_id == "none"
    denied = BackendDecisionProvider(Provider())
    try:
        await denied.bind_run(user_id=other_id, run_id=run_id)
    except RuntimeError:
        assert denied.run_id is None
    else:
        raise AssertionError("Cross-owner routing bind must fail")

    async with SessionFactory() as session:
        metrics = await routing_metrics(session, days=1, run_id=run_id)
        assert metrics["decisions"] == 2 and metrics["safe_stops"] == 1
        assert metrics["fallback_decisions"] == 0 and metrics["unknown_usage_decisions"] == 0
        assert (await routing_metrics(session, run_id=run_id, mode="shadow"))["decisions"] == 0
        assert metrics["input_tokens"] == 21 and metrics["output_tokens"] == 0
        assert tokens == [21]
        raw = json.dumps(metrics)
        assert "owner@example.test" not in raw and "private-result-marker" not in raw
        assert metrics["recent"][0]["model_version"] is not None
        run = await session.get(AgentRun, run_id)
        await session.delete(run)
        await session.commit()
        assert (
            await session.scalar(
                select(func.count(RoutingTrace.id)).where(RoutingTrace.agent_run_id == run_id)
            )
            == 0
        )
    await engine.dispose()
    return user_id


def check_scratch():
    settings = get_settings()
    if not (
        settings.db_name == "routing_checks" or settings.db_name.startswith("routing_checks_")
    ) or settings.db_host not in {"127.0.0.1", "postgres"}:
        raise RuntimeError("Routing probe requires the isolated routing_checks database")
    if settings.openrouter_api_key or settings.deepseek_api_key:
        raise RuntimeError("Routing database check must have empty provider keys")
    config = Config(str(PROJECT_ROOT / "alembic.ini"), stdout=sys.stderr)
    sync_engine = create_engine(settings.migration_database_url)
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    command.upgrade(config, "head")
    command.check(config)
    sentinel = asyncio.run(check_database())
    command.downgrade(config, "20261018_0024")
    assert "routing_traces" not in inspect(sync_engine).get_table_names()
    with sync_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM users WHERE id=:id"), {"id": sentinel})
            == 1
        )
    command.upgrade(config, "head")
    command.check(config)
    sync_engine.dispose()
    result = {
        "status": "PASS",
        "head": "20261019_0025",
        "provider_requests": 0,
        "persistent_usage_and_model": True,
        "resume_budget": True,
        "cross_owner_rejected": True,
        "quota_tokens_recorded": True,
        "raw_payload_absent": True,
        "run_delete_cascades_trace": True,
        "migration_roundtrip_preserves_user": True,
        "metadata_drift": False,
    }
    if settings.db_host == "127.0.0.1":
        folder = Path("reports/checks/2026-10-19-m2-runtime")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "postgres-check.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    return result


def check():
    settings = get_settings()
    if settings.db_name == "routing_checks" or settings.db_name.startswith("routing_checks_"):
        with redirect_stdout(sys.stderr):
            return check_scratch()
    if settings.app_env != "development" or settings.db_host != "postgres":
        raise RuntimeError("Routing check requires an isolated development database")
    database = f"routing_checks_{uuid4().hex}"
    assert database.isidentifier() and database != settings.db_name
    admin = create_engine(
        settings.migration_database_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        child = subprocess.run(
            [sys.executable, "-m", "scripts.check_routing_runtime"],
            env={
                **os.environ,
                "DB_NAME": database,
                "OPENROUTER_API_KEY": "",
                "DEEPSEEK_API_KEY": "",
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        if child.returncode:
            raise RuntimeError("Scratch routing check failed")
        result = json.loads(child.stdout)
        result["scratch_database_removed"] = True
        return result
    finally:
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))
