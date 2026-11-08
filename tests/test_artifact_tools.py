import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_artifact_api import api  # noqa: F401

from agent_core.tools import ToolContext, ToolExecutor, ToolRegistry
from agent_core.tools.artifacts import register_artifact_tools
from artifact_runtime.protocol import ArtifactOperation
from backend.models import AgentRun
from backend.models.artifact import ArtifactVersion
from backend.routes import artifacts as routes
from backend.services.artifacts.tools import ArtifactToolService


@pytest.fixture
async def bridge(api):  # noqa: F811
    _, sessions, storage, owner, other, conversation, _ = api
    run = AgentRun(id=uuid4(), conversation_id=conversation.id, status="running")
    async with sessions() as session:
        session.add(run)
        await session.commit()
    service = ArtifactToolService(
        service=storage,
        sessions=sessions,
        runner_factory=lambda user: routes.runner_client(storage, user),
    )
    context = ToolContext(
        user_id=owner.id,
        conversation_id=conversation.id,
        user_role="user",
        run_id=run.id,
        operation_key="call-1",
    )
    return service, context, sessions


def plan(value=10):
    return {
        "operations": [
            {"kind": "write_cells", "sheet": "Sheet1", "start": "A1", "values": [[value], [20]]},
            {"kind": "set_formula", "sheet": "Sheet1", "cell": "A3", "formula": "=SUM(A1:A2)"},
        ]
    }


async def test_create_edit_tools_publish_once_after_validation(bridge):
    service, context, sessions = bridge
    operation = ArtifactOperation(kind="create_xlsx", payload=plan())
    first = await service.execute(context, operation)
    assert not first.is_error, first
    ref = first.data["artifact_refs"][0]
    assert ref["calculation_status"] == "fresh"
    assert (await service.execute(context, operation)).data["artifact_refs"] == [ref]
    # Conversation outputs remain usable without changing pinned input versions.
    edit = ArtifactOperation(
        kind="edit_xlsx", parent_version_id=ref["version_id"], payload=plan(15)
    )
    second = await service.execute(replace(context, operation_key="call-2"), edit)
    assert not second.is_error, second
    child = second.data["artifact_refs"][0]
    assert child["parent_version_id"] == ref["version_id"]
    assert child["version_id"] != ref["version_id"]
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.owner_user_id == context.user_id)
            )
            == 2
        )
    encoded = json.dumps(second.data)
    assert "storage_key" not in encoded and "base64" not in encoded and "/output" not in encoded
    changed = await service.execute(
        context, ArtifactOperation(kind="create_xlsx", payload=plan(99))
    )
    assert changed.is_error


async def test_tool_cannot_open_unselected_version(bridge):
    service, context, _ = bridge
    result = await service.inspect(context, version_id=uuid4())
    assert result.is_error and result.error_code == "artifact_not_in_scope"


async def test_owned_uploaded_input_requires_selection(bridge, api):  # noqa: F811
    from test_artifact_api import upload

    service, context, _ = bridge
    ref = await upload(api[0])
    version = UUID(ref["version_id"])
    denied = await service.inspect(context, version_id=version)
    assert denied.error_code == "artifact_not_in_scope"
    allowed = await service.inspect(
        replace(context, artifact_version_ids=(version,)), version_id=version
    )
    assert not allowed.is_error
    assert "uploaded" in allowed.content


def test_immutable_output_approval_exception_is_host_only():
    from pydantic import ValidationError

    from agent_core.contracts import CapabilityDescriptor

    with pytest.raises(ValidationError):
        CapabilityDescriptor(
            capability_id="plugin_write",
            kind="tool",
            name="write",
            description="plugin",
            version="1",
            plugin_id="external",
            side_effect="write",
            requires_approval=False,
            immutable_output=True,
        )


async def test_conversation_output_scope_survives_run_retention(bridge):
    from backend.models import Message
    from backend.services.artifacts.publication import bind_message_artifacts

    service, context, sessions = bridge
    first = await service.execute(context, ArtifactOperation(kind="create_xlsx", payload=plan()))
    ref = first.data["artifact_refs"][0]
    async with sessions() as session:
        message = Message(
            id=uuid4(), conversation_id=context.conversation_id, role="assistant", content="Saved"
        )
        session.add(message)
        await session.flush()
        await bind_message_artifacts(
            session, context.user_id, context.conversation_id, message.id, [ref]
        )
        await session.delete(await session.get(AgentRun, context.run_id))
        run = AgentRun(id=uuid4(), conversation_id=context.conversation_id, status="running")
        session.add(run)
        await session.commit()
    result = await service.inspect(
        replace(context, run_id=run.id), version_id=UUID(ref["version_id"])
    )
    assert not result.is_error, result


async def test_python_fallback_requires_approval(bridge):
    service, context, _ = bridge
    registry = ToolRegistry()
    register_artifact_tools(registry, service)
    result = await ToolExecutor().execute(
        registry,
        context,
        "run_artifact_python",
        {
            "name": "report.xlsx",
            "code": "raise RuntimeError('must not execute')",
        },
    )
    assert result.error_code == "tool_approval_required"
    assert not registry.get("artifact_edit").policy.requires_approval


async def test_invalid_output_never_published(bridge):
    service, context, sessions = bridge
    result = await service.execute(
        context,
        ArtifactOperation(
            kind="python",
            code="from pathlib import Path; Path('/output/output-0.xlsx').write_bytes(b'bad')",
        ),
    )
    assert result.is_error
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.owner_user_id == context.user_id)
            )
            == 0
        )


async def test_cancel_before_publication_has_no_version(bridge):
    service, context, sessions = bridge
    calls = 0

    async def stop(_):
        nonlocal calls
        calls += 1
        if calls >= 2:
            import asyncio

            raise asyncio.CancelledError

    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            replace(context, authorization_guard=stop),
            ArtifactOperation(kind="create_xlsx", payload=plan()),
        )
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.owner_user_id == context.user_id)
            )
            == 0
        )


def test_file_task_routes_to_tools_not_rag_only():
    from agent_core.routing.policy import PrivacyPolicy
    from agent_core.routing.query_router import build_query_request

    request = build_query_request(
        [{"role": "user", "content": "Sửa Excel này"}],
        selected_artifacts=[{"name": "budget.xlsx", "version_id": str(UUID(int=1))}],
    )
    assert json.loads(request.state)["selected_artifacts"][0]["name"] == "budget.xlsx"
    assert "run_python_code" in request.instructions
    prepared, _ = PrivacyPolicy().prepare(request)
    assert json.loads(prepared.state)["selected_artifacts"][0]["name"] == "budget.xlsx"


@pytest.mark.parametrize("extension", ["docx", "pdf"])
def test_working_document_routing_keeps_its_type(extension):
    from agent_core.routing.policy import PrivacyPolicy
    from agent_core.routing.query_router import build_query_request

    request = build_query_request(
        [{"role": "user", "content": "Edit this document"}],
        selected_artifacts=[{"name": "report." + extension}],
    )
    prepared, _ = PrivacyPolicy().prepare(request)
    assert json.loads(prepared.state)["selected_artifacts"][0]["type"] == extension


def test_workspace_catalog_fits_chat_schema_allowance():
    from types import SimpleNamespace

    from agent_core.tools.selector import ToolSelector
    from backend.routes.chat import chat_tool_selection_config

    settings = SimpleNamespace(
        workspace_artifacts_enabled=True,
        tool_lazy_load_threshold=8,
        tool_max_loaded=4,
        google_connector_enabled=False,
    )
    registry = ToolRegistry()
    register_artifact_tools(registry, object())
    names = tuple(tool.name for tool in registry.list_tools())
    config = chat_tool_selection_config(settings)
    selected = ToolSelector(config).fit(
        registry, "user", names, mode="all", required=names, max_tools=len(names)
    )
    assert selected.loaded_names == frozenset(names)
    settings.workspace_artifacts_enabled = False
    assert chat_tool_selection_config(settings).schema_token_budget == 6000
