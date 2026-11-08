# ruff: noqa: F811 -- imported shared fixture is intentionally injected by pytest.
import os

import pytest
from automation_fixtures import queued, runtime
from google_fixtures import google_db  # noqa: F401
from httpx import ASGITransport
from sqlalchemy import func, select
from test_automation_api import draft


@pytest.mark.parametrize("allowed", [True, False])
async def test_automation_python_has_no_approval_and_enforces_file_grants(
    google_db,
    monkeypatch,
    tmp_path,
    allowed,  # noqa: F811
):
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires real Docker unified runtime image")
    from agent_core.tools import ToolRegistry
    from agent_core.tools.python import register_python_tool
    from artifact_runtime.app import create_app
    from artifact_runtime.containers import DockerBroker
    from backend import dependencies_tools
    from backend.background.handlers import build_background_job_registry
    from backend.background.queue import PostgresJobQueue
    from backend.background.worker import BackgroundWorker
    from backend.models import AgentApproval, ArtifactVersion
    from backend.models.automation import AutomationRun
    from backend.services.artifacts.python import PythonToolService
    from backend.services.artifacts.runner_client import ArtifactRunnerClient
    from backend.services.artifacts.service import ArtifactService
    from backend.services.artifacts.storage import ArtifactStorage
    from scripts.python_fixture import OfflinePythonModel

    db = google_db
    settings, _ = runtime(db, monkeypatch, OfflinePythonModel())
    settings.workspace_artifacts_enabled = True
    settings.python_sandbox_enabled = True
    storage = ArtifactService(ArtifactStorage(tmp_path / "artifacts"))
    broker = create_app(
        DockerBroker(tmp_path / "broker", docker=os.environ.get("ARTIFACT_DOCKER", "docker")),
        key="k" * 32,
    )
    service = PythonToolService(
        service=storage,
        sessions=db.sessions,
        publication_enabled=True,
        runner_factory=lambda owner: ArtifactRunnerClient(
            "http://runner",
            key="k" * 32,
            storage=storage.storage,
            owner=owner,
            transport=ASGITransport(app=broker),
        ),
    )
    registry = ToolRegistry()
    register_python_tool(registry, service)
    monkeypatch.setattr(dependencies_tools, "get_tool_registry", lambda: registry)
    _, identity = await queued(
        db,
        body=draft(
            instruction="Create an Excel report",
            artifact_actions=["create", "inspect"] if allowed else [],
        ),
    )
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name="default",
        worker_id="unified-python-automation-qa",
        control_poll_seconds=0.1,
    )
    assert await worker.run_once()
    async with db.sessions() as session:
        run = await session.get(AutomationRun, identity)
        # The strict offline model raises on a denied tool observation. Its
        # interrupted model receipt correctly needs reconciliation; no Python
        # execution/file publication or approval is allowed in that case.
        assert run.status == ("succeeded" if allowed else "needs_reconciliation"), run.result
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentApproval)
                .where(AgentApproval.agent_run_id == run.agent_run_id)
            )
            == 0
        )
        assert await session.scalar(
            select(func.count())
            .select_from(ArtifactVersion)
            .where(ArtifactVersion.source_run_id == run.agent_run_id)
        ) == int(allowed)
