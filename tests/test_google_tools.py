import json
from dataclasses import replace
from uuid import uuid4

import pytest
from google_fixtures import google_db  # noqa: F401
from test_google_file_access import selected

from agent_core.tools import ToolExecutor, ToolRegistry
from backend.models import AgentRun


async def tools(db):
    from agent_core.tools.google import register_google_tools
    from backend.services.google.tools import GoogleToolService

    access, context, ref = await selected(db)
    run = AgentRun(id=uuid4(), conversation_id=db.conversation, status="running")
    async with db.sessions() as session:
        session.add(run)
        await session.commit()
    service = GoogleToolService(access=access, sessions=db.sessions)
    context = replace(context, run_id=run.id, operation_key="prepare-1")
    context = await service.pin(context)
    registry = ToolRegistry()
    register_google_tools(registry, service)
    context = replace(context, authorization_guard=lambda name: service.guard(context, name))
    return registry, context, ref, service


async def test_google_tools_read_prepare_approve_and_replay(google_db):  # noqa: F811
    db = google_db
    registry, context, ref, _ = await tools(db)
    executor = ToolExecutor()
    read = await executor.execute(
        registry,
        context,
        "google_sheets_read",
        {"ref": ref.model_dump(mode="json"), "range_a1": "Sheet1!A1:B100"},
    )
    assert not read.is_error and "Existing" in read.content
    prepared = await executor.execute(
        registry,
        context,
        "google_sheets_prepare",
        {
            "ref": ref.model_dump(mode="json"),
            "change": {
                "kind": "append_rows",
                "range_a1": "Sheet1!A1:B100",
                "rows": [["https://new.example/repo", "=literal"]],
                "dedup_column": 0,
            },
        },
    )
    assert not prepared.is_error, prepared
    args = prepared.data["commit_arguments"]
    assert (
        await executor.execute(registry, context, "google_sheets_append", args)
    ).error_code == "tool_approval_required"
    denied = await executor.execute(
        registry, context, "google_sheets_append", {**args, "approved": True}, approval_granted=True
    )
    assert denied.error_code == "tool_schema_validation_failed"
    first = await executor.execute(
        registry, context, "google_sheets_append", args, approval_granted=True
    )
    assert not first.is_error, first
    replay = await executor.execute(
        registry, context, "google_sheets_append", args, approval_granted=True
    )
    assert replay.data == first.data and len(db.provider.writes) == 1
    encoded = json.dumps([read.content, prepared.data, first.data])
    assert "canary" not in encoded and "storage_key" not in encoded


async def test_google_pinned_scope_revoke_blocks_old_schema_and_context(google_db):  # noqa: F811
    db = google_db
    registry, context, ref, service = await tools(db)
    executor = ToolExecutor()
    args = {"ref": ref.model_dump(mode="json"), "range_a1": "A1:B10"}
    assert not (await executor.execute(registry, context, "google_sheets_read", args)).is_error
    async with db.sessions() as session:
        await service.access.revoke_file(session, db.owner, ref.connection_id, ref.file_id)
        await service.access.set_files(session, db.owner, ref.connection_id, [ref.file_id])
        await service.access.set_work_files(session, db.owner, db.conversation, [ref])
    result = await executor.execute(registry, context, "google_sheets_read", args)
    assert result.error_code == "google_authorization_changed"
    foreign = await service.invoke(replace(context, user_id=db.other), "google_sheets_read", args)
    assert foreign.is_error


def test_google_catalog_closed_and_bounded():
    from agent_core.tools.google import register_google_tools
    from backend.services.google.tools import GoogleToolService

    registry = ToolRegistry()
    register_google_tools(registry, GoogleToolService())
    schemas = [
        registry.model_definition(entry.tool.name)
        for entry in registry.list_accessible_tools("user")
    ]
    encoded = json.dumps(schemas)
    assert "refresh_token" not in encoded and "access_token" not in encoded
    assert "google_workspace" not in encoded
    assert len(encoded) < 60000
    for name in ("google_sheets_append", "google_sheets_update"):
        entry = registry.get(name)
        assert entry.policy.requires_approval and not entry.policy.idempotent
        assert entry.policy.max_retries == 0


@pytest.mark.parametrize("scenario", ["read", "research", "update"])
async def test_google_real_worker_graph_with_model_fixture(google_db, scenario):  # noqa: F811
    from scripts.google_fixture import run_google_scenario

    report = await run_google_scenario(google_db, scenario)
    assert report["status"] == "completed", report
    assert report["search_calls"] == (1 if scenario == "research" else 0)
    assert report["approvals"] == (0 if scenario == "read" else 1)
    assert report["writes"] == (0 if scenario == "read" else 1)
