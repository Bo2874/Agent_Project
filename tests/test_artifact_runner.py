import asyncio
import io
import os
import time
import zipfile
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from artifact_runtime.containers import DockerBroker
from artifact_runtime.protocol import (
    ArtifactOperation,
    ArtifactRunRequest,
    BlobDescriptor,
    collect_outputs,
)


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "attacker/image"),
        ("mounts", ["/:/host"]),
        ("environment", {"TOKEN": "secret"}),
        ("profile", "privileged"),
    ],
)
def test_runner_refuses_arbitrary_image_mount_and_environment(field, value):
    with pytest.raises(ValidationError):
        ArtifactRunRequest.model_validate(
            {
                "job_id": str(uuid4()),
                "operation": {"kind": "python", "code": "pass"},
                field: value,
            }
        )


def test_output_symlink_or_oversize_rejected(tmp_path):
    (tmp_path / "too-big.xlsx").write_bytes(b"a" * 101)
    with pytest.raises(ValueError):
        collect_outputs(tmp_path, max_file_bytes=100)
    (tmp_path / "too-big.xlsx").unlink()
    try:
        (tmp_path / "link.xlsx").symlink_to(tmp_path / "outside")
    except OSError:
        pytest.skip("Symlink privilege unavailable on this host; Linux Docker gate also checks it")
    with pytest.raises(ValueError):
        collect_outputs(tmp_path)


@pytest.fixture
def real_broker(tmp_path):
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Set ARTIFACT_RUNNER_DOCKER=1 after building sandbox images")
    return DockerBroker(tmp_path, docker=os.environ.get("ARTIFACT_DOCKER", "docker"))


async def test_input_is_readonly_and_network_blocked(real_broker, tmp_path):
    code = """
from pathlib import Path
import socket
try:
    Path('/input/input-0.xlsx').write_bytes(b'changed')
    raise AssertionError('input writable')
except PermissionError: pass
except OSError: pass
try:
    socket.create_connection(('1.1.1.1',443),timeout=1)
    raise AssertionError('network accessible')
except OSError: pass
assert not Path('/var/run/docker.sock').exists()
assert not any('SECRET' in k or 'TOKEN' in k or 'KEY' in k for k in __import__('os').environ)
Path('/output/check.xlsx').write_bytes(b'isolation verified')
"""
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    content = content.getvalue()
    result = await real_broker.run(
        ArtifactRunRequest(
            job_id=uuid4(),
            operation=ArtifactOperation(kind="python", code=code),
            inputs=(BlobDescriptor.from_bytes("input-0.xlsx", content),),
        ),
        (io.BytesIO(content),),
    )
    assert result.exists()


async def test_cancel_reaps_child_and_volume(real_broker):
    request = ArtifactRunRequest(
        job_id=uuid4(),
        operation=ArtifactOperation(
            kind="python",
            code="import time; time.sleep(120)",
        ),
    )
    task = asyncio.create_task(real_broker.run(request, ()))
    for _ in range(150):
        running = await real_broker.command(
            "ps",
            "--format",
            "{{.Names}}",
            "--filter",
            f"label=personal-agent.artifact-job={request.job_id}",
        )
        if f"artifact-{request.job_id}-exec" in running:
            break
        await asyncio.sleep(0.1)
    assert f"artifact-{request.job_id}-exec" in running
    await asyncio.sleep(0.3)
    assert await real_broker.resources(request.job_id)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await real_broker.resources(request.job_id) == []


async def test_completed_job_replay_never_executes_twice(real_broker):
    request = ArtifactRunRequest(
        job_id=uuid4(),
        operation=ArtifactOperation(
            kind="python",
            code="from pathlib import Path; Path('/output/test.xlsx').write_bytes(b'first')",
        ),
    )
    first = await real_broker.run(request, ())
    before = first.stat().st_mtime_ns
    second = await real_broker.run(request, ())
    assert first == second
    assert second.stat().st_mtime_ns == before
    restarted = DockerBroker(real_broker.root, docker=real_broker.docker)
    assert await restarted.run(request, ()) == first


async def test_unsigned_request_rejected(tmp_path):
    from artifact_runtime.app import create_app

    broker = DockerBroker(tmp_path)
    app = create_app(broker, key="k" * 32)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://runner") as client:
        response = await client.delete(f"/v1/jobs/{uuid4()}")
    assert response.status_code == 401


async def test_binary_transport_round_trip(real_broker, tmp_path):
    from artifact_runtime.app import create_app
    from backend.services.artifacts.runner_client import ArtifactRunnerClient
    from backend.services.artifacts.storage import ArtifactStorage

    app = create_app(real_broker, key="k" * 32)
    storage = ArtifactStorage(tmp_path / "backend")
    client = ArtifactRunnerClient(
        "http://runner",
        key="k" * 32,
        storage=storage,
        owner=uuid4(),
        transport=ASGITransport(app=app),
    )

    async def cancel():
        return None

    result = await client.execute(
        ArtifactRunRequest(
            job_id=uuid4(),
            operation=ArtifactOperation(
                kind="python",
                code=(
                    "from pathlib import Path\nPath('/output/large.xlsx').write_bytes(b'a'*524288)"
                ),
            ),
        ),
        (),
        cancel=cancel,
    )
    assert result.files[0].size_bytes == 524288
    assert storage.path(result.files[0].key).read_bytes() == b"a" * 524288


@pytest.mark.parametrize(
    "code",
    [
        "from pathlib import Path; Path('/output/link.xlsx').symlink_to('/etc/passwd')",
        "from pathlib import Path; Path('/output/large.xlsx').write_bytes(b'a'*20971521)",
    ],
)
async def test_linux_output_symlink_and_oversize_never_return_bundle(real_broker, code):
    from artifact_runtime.containers import RunnerError

    request = ArtifactRunRequest(
        job_id=uuid4(), operation=ArtifactOperation(kind="python", code=code)
    )
    with pytest.raises(RunnerError):
        await real_broker.run(request, ())
    assert await real_broker.resources(request.job_id) == []


async def test_tampered_manifest_and_nonce_replay_rejected(tmp_path):
    from artifact_runtime.app import create_app
    from artifact_runtime.auth import signed_headers
    from artifact_runtime.protocol import bundle_header

    app = create_app(DockerBroker(tmp_path), key="k" * 32)
    headers = signed_headers("k" * 32, "POST", "/v1/jobs", b"{}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://runner") as client:
        tampered = await client.post("/v1/jobs", content=bundle_header({"x": 1}), headers=headers)
        assert tampered.status_code == 401
        malformed = await client.post("/v1/jobs", content=bundle_header({}), headers=headers)
        assert malformed.status_code == 422
        replay = await client.post("/v1/jobs", content=bundle_header({}), headers=headers)
        assert replay.status_code == 401


async def test_cancel_before_start_prevents_late_execution(tmp_path):
    from artifact_runtime.containers import RunnerError

    broker = DockerBroker(tmp_path, docker="must-not-run")
    request = ArtifactRunRequest(
        job_id=uuid4(), operation=ArtifactOperation(kind="python", code="pass")
    )
    broker.mark_cancelled(request.job_id)
    with pytest.raises(RunnerError, match="cancelled"):
        await broker.run(request, ())


async def test_http_cancel_reaps_running_job(real_broker, tmp_path):
    from artifact_runtime.app import create_app
    from backend.services.artifacts.runner_client import ArtifactRunnerClient
    from backend.services.artifacts.storage import ArtifactStorage

    storage = ArtifactStorage(tmp_path / "backend")
    client = ArtifactRunnerClient(
        "http://runner",
        key="k" * 32,
        storage=storage,
        owner=uuid4(),
        transport=ASGITransport(app=create_app(real_broker, key="k" * 32)),
    )
    request = ArtifactRunRequest(
        job_id=uuid4(),
        operation=ArtifactOperation(
            kind="python",
            code="import time; time.sleep(120)",
        ),
    )
    stop = asyncio.Event()

    async def cancel():
        if stop.is_set():
            raise asyncio.CancelledError()

    task = asyncio.create_task(client.execute(request, (), cancel=cancel))
    for _ in range(150):
        names = await real_broker.command(
            "ps",
            "--format",
            "{{.Names}}",
            "--filter",
            f"label=personal-agent.artifact-job={request.job_id}",
        )
        if f"artifact-{request.job_id}-exec" in names:
            break
        await asyncio.sleep(0.1)
    assert f"artifact-{request.job_id}-exec" in names
    stop.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await real_broker.resources(request.job_id) == []
    assert list(storage.root.rglob("content.xlsx")) == []


async def test_reaper_removes_expired_orphan_volume_and_preserves_foreign_label(real_broker):
    job, foreign_job = uuid4(), uuid4()
    owned, foreign = f"artifact-input-{job}", f"artifact-input-{foreign_job}"
    await real_broker.command(
        "volume",
        "create",
        "--label",
        "personal-agent.artifact-runner=1",
        "--label",
        f"personal-agent.artifact-job={job}",
        "--label",
        f"personal-agent.artifact-created={time.time() - 200}",
        owned,
    )
    await real_broker.command(
        "volume",
        "create",
        "--label",
        "personal-agent.artifact-runner=foreign",
        foreign,
    )
    try:
        await real_broker.reap_stale()
        assert await real_broker.resources(job) == []
        assert await real_broker.command("volume", "inspect", foreign)
    finally:
        await real_broker.cleanup(job)
        await real_broker.command("volume", "rm", foreign)
