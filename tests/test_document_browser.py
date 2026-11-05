import asyncio
import json
import os
import shutil
import socket
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import uvicorn
from docx import Document
from pydantic import SecretStr
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.engine import make_url
from test_artifact_api import api  # noqa: F401

from backend.main import app
from backend.models import AgentRun, BackgroundJob
from backend.models.artifact import ArtifactVersion
from backend.routes import artifacts, pages
from scripts.artifact_fixture import OfflineArtifactRuntime


async def test_real_browser_worker_document_workflow(api, tmp_path, monkeypatch):  # noqa: F811
    if not shutil.which("node"):
        pytest.skip("Requires Node and Chrome")
    _, sessions, service, owner, _, conversation, settings = api
    url = make_url(os.environ["ARTIFACT_TEST_DATABASE_URL"])
    config = settings.model_copy(
        update={
            "db_host": url.host,
            "db_port": url.port,
            "db_name": url.database,
            "db_user": url.username,
            "db_password": SecretStr(url.password),
            "memory_extraction_enabled": False,
            "agent_react_enabled": True,
            "v2_enabled": True,
            "v2_events_enabled": True,
            "background_queue_name": f"documents-{uuid4().hex[:12]}",
        }
    )
    monkeypatch.setattr(pages, "get_settings", lambda: config)
    from backend.dependencies_tools import get_tool_registry as original_registry_dependency

    runtime = await OfflineArtifactRuntime(
        config,
        sessions,
        service,
        runner_factory=lambda user: artifacts.runner_client(service, user),
    ).start()
    app.dependency_overrides[original_registry_dependency] = lambda: runtime.registry
    upload = tmp_path / "uploaded-word.docx"
    document = Document()
    document.add_paragraph("Uploaded document to preserve")
    document.add_paragraph("Original body stays available")
    document.save(upload)
    directory = Path(os.environ.get("ARTIFACT_DOCUMENT_BROWSER_DIR", tmp_path / "browser"))
    formats = os.environ.get("ARTIFACT_DOCUMENT_FORMATS", "docx,pdf")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="off")
    )
    web = asyncio.create_task(server.serve(sockets=[sock]))
    stop = asyncio.Event()

    async def work():
        while not stop.is_set():
            await runtime.worker.run_once()
            await asyncio.sleep(0.05)

    worker = asyncio.create_task(work())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.05)
        process = await asyncio.create_subprocess_exec(
            "node",
            "tests/document_workflow_browser.cjs",
            f"http://127.0.0.1:{port}",
            str(conversation.id),
            str(upload),
            str(directory),
            formats,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async with asyncio.timeout(600):
            output, _ = await process.communicate()
        assert process.returncode == 0, output.decode("utf-8", "replace")
        report = json.loads((directory / "browser.json").read_text(encoding="utf-8"))
        async with sessions() as session:
            runs = (
                await session.scalars(
                    select(AgentRun).where(AgentRun.conversation_id == conversation.id)
                )
            ).all()
            assert runs and all(run.status == "completed" for run in runs), [
                (run.status, run.error_message) for run in runs
            ]
            jobs = (
                await session.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.agent_run_id.in_([run.id for run in runs])
                    )
                )
            ).all()
            assert len(jobs) == len(runs) and all(job.status == "succeeded" for job in jobs)
            for format in report["formats"]:
                old = await session.get(ArtifactVersion, UUID(report[f"{format}_original"]))
                new = await session.get(ArtifactVersion, UUID(report[f"{format}_revised"]))
                assert new.parent_version_id == old.id
                if format == "docx":
                    assert (
                        Document(service.storage.path(old.storage_key)).paragraphs[0].text
                        == "Báo cáo mẫu"
                    )
                    assert (
                        Document(service.storage.path(new.storage_key)).paragraphs[0].text
                        == "Báo cáo đã sửa"
                    )
                    uploaded = await session.get(ArtifactVersion, UUID(report["uploaded_original"]))
                    assert (
                        service.storage.path(uploaded.storage_key).read_bytes()
                        == upload.read_bytes()
                    )
                else:
                    text = "\n".join(
                        page.extract_text()
                        for page in PdfReader(service.storage.path(new.storage_key)).pages
                    )
                    assert "Báo cáo đã sửa" in text
        if target := os.environ.get("ARTIFACT_GATE_REPORT"):
            Path(target).write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        stop.set()
        await worker
        server.should_exit = True
        await web
        await runtime.close()
