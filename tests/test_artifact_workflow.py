import asyncio
import hashlib
import io
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from openpyxl import load_workbook
from pydantic import SecretStr
from sqlalchemy import func, select
from test_artifact_tools import api, bridge, plan  # noqa: F401

from artifact_runtime.protocol import ArtifactOperation
from backend.models import AgentOperation, RunStep, ToolCall
from backend.models.artifact import ArtifactVersion
from backend.services.artifacts.maintenance import reconcile_orphans, recover_artifact_operations


async def test_real_worker_graph_and_postgres_checkpoints(api):  # noqa: F811
    from sqlalchemy.engine import make_url

    from backend.models import AgentRun, BackgroundJob, RunEvent
    from scripts.artifact_fixture import OfflineArtifactRuntime

    _, sessions, storage, owner, _, conversation, settings = api
    url = make_url(os.environ["ARTIFACT_TEST_DATABASE_URL"])
    config = settings.model_copy(
        update=dict(
            db_host=url.host,
            db_port=url.port,
            db_name=url.database,
            db_user=url.username,
            db_password=SecretStr(url.password),
            memory_extraction_enabled=False,
            agent_react_enabled=True,
            background_queue_name=f"artifact-{uuid4().hex[:12]}",
        )
    )
    from backend.routes.artifacts import runner_client

    runtime = await OfflineArtifactRuntime(
        config, sessions, storage, runner_factory=lambda user: runner_client(storage, user)
    ).start()
    try:
        run_id = await runtime.enqueue(owner.id, conversation.id, "create Excel")
        assert await runtime.worker.run_once()
        async with sessions() as session:
            run = await session.get(AgentRun, run_id)
            assert run.status == "completed", (run.status, run.stop_reason, run.error_message)
            job = await session.scalar(
                select(BackgroundJob).where(BackgroundJob.agent_run_id == run_id)
            )
            assert job.status == "succeeded", (job.status, job.error_message)
            versions = (
                await session.scalars(
                    select(ArtifactVersion).where(ArtifactVersion.source_run_id == run_id)
                )
            ).all()
            assert len(versions) == 1 and versions[0].calculation_status == "not_calculated"
            assert await session.scalar(
                select(RunEvent.id).where(
                    RunEvent.agent_run_id == run_id, RunEvent.kind == "artifact_ready"
                )
            )
    finally:
        await runtime.close()


@pytest.mark.skipif(
    not os.environ.get("ARTIFACT_GATE_RUNNER_URL"), reason="Requires offline gate broker"
)
async def test_hard_worker_crash_after_publish_then_resume_once(api, tmp_path):  # noqa: F811
    import subprocess
    import sys

    from sqlalchemy import update
    from sqlalchemy.engine import make_url

    from backend.models import AgentRun, BackgroundJob
    from scripts.artifact_fixture import OfflineArtifactRuntime

    _, sessions, storage, owner, _, conversation, settings = api
    url = make_url(os.environ["ARTIFACT_TEST_DATABASE_URL"])
    config = settings.model_copy(
        update=dict(
            db_host=url.host,
            db_port=url.port,
            db_name=url.database,
            db_user=url.username,
            db_password=SecretStr(url.password),
            memory_extraction_enabled=False,
            agent_react_enabled=True,
            background_queue_name=f"artifact-{uuid4().hex[:12]}",
        )
    )
    runtime = await OfflineArtifactRuntime(config, sessions, storage).start()
    marker = tmp_path / "crash-marker.txt"
    environment = {
        **os.environ,
        "ARTIFACT_FIXTURE_QUEUE": config.background_queue_name,
        "ARTIFACT_FIXTURE_STORAGE": str(storage.storage.root),
        "ARTIFACT_FIXTURE_CRASH_MARKER": str(marker),
    }
    try:
        run_id = await runtime.enqueue(owner.id, conversation.id, "create Excel")
        process = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "scripts.artifact_fixture_worker"],
            env=environment,
            capture_output=True,
            timeout=120,
        )
        assert process.returncode == 86, process.stderr.decode("utf-8", "replace")[-4000:]
        assert marker.exists()
        async with sessions() as session:
            await session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.agent_run_id == run_id)
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        environment.pop("ARTIFACT_FIXTURE_CRASH_MARKER")
        process = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "scripts.artifact_fixture_worker"],
            env=environment,
            capture_output=True,
            timeout=120,
        )
        assert process.returncode == 0, process.stderr.decode("utf-8", "replace")[-4000:]
        async with sessions() as session:
            run = await session.get(AgentRun, run_id)
            assert run.status == "completed", (run.status, run.stop_reason, run.error_message)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ArtifactVersion)
                    .where(ArtifactVersion.source_run_id == run_id)
                )
                == 1
            )
    finally:
        await runtime.close()


async def test_old_orphans_cleaned_but_referenced_and_recent_files_survive(bridge):  # noqa: F811
    service, context, sessions = bridge
    storage = service.storage_service().storage
    ref = (
        await service.execute(context, ArtifactOperation(kind="create_xlsx", payload=plan()))
    ).data["artifact_refs"][0]
    old = storage.put(
        io.BytesIO(b"old"), f"{context.user_id}/{uuid4()}/{uuid4()}/content.xlsx", max_bytes=100
    )
    fresh = storage.put(
        io.BytesIO(b"new"), f"{context.user_id}/{uuid4()}/{uuid4()}/content.xlsx", max_bytes=100
    )
    async with sessions() as session:
        version = await session.get(ArtifactVersion, UUID(ref["version_id"]))
    timestamp = time.time() - 90000
    for key in (old.key, version.storage_key):
        os.utime(storage.path(key), (timestamp, timestamp))
    assert await reconcile_orphans(sessions, storage, dry_run=True) == 1
    assert storage.path(old.key).exists()
    assert await reconcile_orphans(sessions, storage) == 1
    assert not storage.path(old.key).exists()
    assert storage.path(fresh.key).exists() and storage.path(version.storage_key).exists()


async def test_known_published_operation_recovers_without_duplicate(bridge):  # noqa: F811
    service, context, sessions = bridge
    context = replace(context, operation_key="root.tool.1.fixture")
    result = await service.execute(context, ArtifactOperation(kind="create_xlsx", payload=plan()))
    assert not result.is_error
    from agent_core.graph.runtime import _fingerprint as fingerprint

    args = {"payload": plan()}
    async with sessions() as session:
        step = RunStep(id=uuid4(), agent_run_id=context.run_id, step_number=1)
        session.add(step)
        await session.flush()
        session.add(
            ToolCall(
                run_step_id=step.id,
                provider_call_id="fixture",
                tool_name="artifact_create",
                arguments=args,
                status="running",
            )
        )
        operation = AgentOperation(
            agent_run_id=context.run_id,
            key=context.operation_key,
            fingerprint=fingerprint({"name": "artifact_create", "arguments": args}),
            status="started",
        )
        session.add(operation)
        await session.commit()
        assert await recover_artifact_operations(session, context.user_id, context.run_id) == 1
        await session.commit()
        await session.refresh(operation)
        assert operation.status == "completed"
        assert operation.result["data"]["artifact_refs"] == result.data["artifact_refs"]


async def test_recovery_keeps_unknown_external_action_blocked(bridge):  # noqa: F811
    from agent_core.graph.runtime import _fingerprint

    _, context, sessions = bridge
    async with sessions() as session:
        step = RunStep(id=uuid4(), agent_run_id=context.run_id, step_number=1)
        session.add(step)
        await session.flush()
        session.add(
            ToolCall(
                run_step_id=step.id,
                provider_call_id="email",
                tool_name="send_email",
                arguments={},
                status="running",
            )
        )
        operation = AgentOperation(
            agent_run_id=context.run_id,
            key="root.tool.1.email",
            fingerprint=_fingerprint({"name": "send_email", "arguments": {}}),
            status="started",
        )
        session.add(operation)
        await session.commit()
        assert await recover_artifact_operations(session, context.user_id, context.run_id) == 0
        await session.refresh(operation)
        assert operation.status == "started"


async def test_cancel_after_runner_before_publish_cleans_stage(bridge, monkeypatch):  # noqa: F811
    service, context, sessions = bridge
    original = service.run
    count = 0

    async def interrupted(*args, **kwargs):
        nonlocal count
        result = await original(*args, **kwargs)
        count += 1
        if count == 4:
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(service, "run", interrupted)
    with pytest.raises(asyncio.CancelledError):
        await service.execute(context, ArtifactOperation(kind="create_xlsx", payload=plan()))
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.owner_user_id == context.user_id)
            )
            == 0
        )
    assert list(service.storage_service().storage.root.rglob("content.xlsx")) == []


async def test_lost_lease_before_publication_cleans_all_staged_files(bridge, monkeypatch):  # noqa: F811
    from backend.background.queue import BackgroundJobOwnershipError
    from backend.services import run_events

    service, context, sessions = bridge

    async def expired(session):
        raise BackgroundJobOwnershipError("Lost test lease")

    monkeypatch.setattr(run_events, "fence", expired)
    with pytest.raises(BackgroundJobOwnershipError):
        await service.execute(context, ArtifactOperation(kind="create_xlsx", payload=plan()))
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactVersion)
                .where(ArtifactVersion.owner_user_id == context.user_id)
            )
            == 0
        )
    assert list(service.storage_service().storage.root.rglob("content.xlsx")) == []


async def test_orphan_reconciliation_reclaims_hard_killed_unpack_workspace(bridge):  # noqa: F811
    from test_artifact_temporary import killed_workspace

    service, _, sessions = bridge
    storage = service.storage_service().storage
    stale = killed_workspace(storage.root, "artifact-stage-")
    old = time.time() - 90000
    os.utime(stale, (old, old))
    assert await reconcile_orphans(sessions, storage, dry_run=True) == 1
    assert stale.exists()
    assert await reconcile_orphans(sessions, storage) == 1
    assert not stale.exists()


@pytest.mark.skipif(
    os.environ.get("ARTIFACT_WORKFLOW_GATE") != "1", reason="Run explicit offline workflow gate"
)
async def test_large_workbook_acceptance_benchmark(bridge, monkeypatch):  # noqa: F811
    service, context, sessions = bridge
    import subprocess

    measurements, stages = [], []
    original = service.run

    async def measured(context, job, **kwargs):
        stop = asyncio.Event()
        started = time.perf_counter()

        async def sample():
            while not stop.is_set():
                sample = await asyncio.to_thread(
                    subprocess.run,
                    [
                        os.environ["ARTIFACT_DOCKER"],
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{json .}}",
                        f"artifact-{job}-exec",
                    ],
                    capture_output=True,
                    timeout=8,
                )
                if sample.returncode == 0:
                    for line in sample.stdout.decode().splitlines():
                        try:
                            measurements.append(json.loads(line))
                        except ValueError:
                            pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.3)
                except TimeoutError:
                    pass

        monitoring = asyncio.create_task(sample())
        try:
            return await original(context, job, **kwargs)
        finally:
            stop.set()
            await monitoring
            stages.append(
                {
                    "action": kwargs.get("action", "execute"),
                    "seconds": round(time.perf_counter() - started, 2),
                }
            )

    monkeypatch.setattr(service, "run", measured)
    code = """from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
book = Workbook(); sheet = book.active; sheet.title = 'Data'
for row in range(1, 10001): sheet.append([row * col for col in range(1, 13)])
sheet['L10000'] = '=SUM(A1:A10000)'
book.create_sheet('Notes')['A1'] = 'Offline acceptance fixture'
chart = BarChart()
chart.add_data(Reference(sheet, min_col=2, min_row=1, max_row=30))
sheet.add_chart(chart, 'O3')
book.save('/output/output-0.xlsx')
"""
    started = time.perf_counter()
    result = await service.execute(
        context, ArtifactOperation(kind="python", code=code), name="acceptance.xlsx"
    )
    assert not result.is_error, result
    first = result.data["artifact_refs"][0]
    storage = service.storage_service().storage
    async with sessions() as session:
        row = await session.get(ArtifactVersion, UUID(first["version_id"]))
    content = storage.path(row.storage_key).read_bytes()
    workbook = load_workbook(io.BytesIO(content), data_only=True)
    assert workbook["Data"]["L10000"].value == 50005000
    assert workbook["Data"].max_row == 10000 and workbook["Data"].max_column == 12
    assert len(workbook.sheetnames) == 2
    formula = load_workbook(io.BytesIO(content))
    assert formula["Data"]["L10000"].value == "=SUM(A1:A10000)"
    assert len(formula["Data"]._charts) == 1
    children = await asyncio.gather(
        *[
            service.execute(
                replace(context, operation_key=f"child-{index}"),
                ArtifactOperation(
                    kind="edit_xlsx",
                    parent_version_id=first["version_id"],
                    payload={
                        "operations": [
                            {
                                "kind": "write_cells",
                                "sheet": "Data",
                                "start": "A1",
                                "values": [[index + 2]],
                            }
                        ]
                    },
                ),
            )
            for index in range(2)
        ]
    )
    assert all(not child.is_error for child in children), children
    assert len({child.data["artifact_refs"][0]["version_id"] for child in children}) == 2
    assert all(
        child.data["artifact_refs"][0]["parent_version_id"] == first["version_id"]
        for child in children
    )
    assert hashlib.sha256(storage.path(row.storage_key).read_bytes()).hexdigest() == first["sha256"]
    report = {
        "rows": 10000,
        "data_columns": 12,
        "sheets": 2,
        "charts": 1,
        "cached_sum": 50005000,
        "create_and_two_concurrent_edits_seconds": round(time.perf_counter() - started, 2),
        "xlsx_bytes": len(content),
        "calculation_status": first["calculation_status"],
        "stages": stages,
        "sample_count": len(measurements),
        "sampled_max_cpu_percent": max(float(item["CPUPerc"].rstrip("%")) for item in measurements),
        "measurement": "Sampled Docker CPU/memory, not exact peak; includes monitoring overhead.",
    }
    assert measurements, "Benchmark must include real CPU/RAM samples"
    import re

    def mib(value):
        match = re.match(r"([0-9.]+)([A-Za-z]+)", value)
        return (
            float(match[1]) * {"B": 1 / 1048576, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[match[2]]
        )

    report["sampled_max_memory_mib"] = round(max(mib(item["MemUsage"]) for item in measurements), 2)
    print("ARTIFACT_BENCHMARK=" + json.dumps(report))
    if destination := os.environ.get("ARTIFACT_GATE_REPORT"):
        from pathlib import Path

        Path(destination).write_text(json.dumps(report, indent=2), encoding="utf-8")
