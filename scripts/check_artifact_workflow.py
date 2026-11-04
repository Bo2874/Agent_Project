"""Explicit offline acceptance: real test PostgreSQL, Docker, worker and model fixture.

Requires ARTIFACT_TEST_DATABASE_URL ending in _test, migrated through current head,
dev dependencies and both locally built artifact images. Never uses a paid model.
"""

import argparse
import asyncio
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    from sqlalchemy.engine import make_url

    try:
        url = make_url(os.environ["ARTIFACT_TEST_DATABASE_URL"])
        if not url.database or not url.database.endswith("_test"):
            raise ValueError("Database name must end in _test")
    except (KeyError, ValueError):
        print("NOT RUN: provide a migrated, task-owned ARTIFACT_TEST_DATABASE_URL ending in _test")
        return 2
    docker = os.environ.get("ARTIFACT_DOCKER") or shutil.which("docker")
    if not docker:
        print("NOT RUN: Docker CLI unavailable")
        return 2
    for image in ("personal-agent-artifact-code:1", "personal-agent-artifact-render:1"):
        if subprocess.run([docker, "image", "inspect", image], capture_output=True).returncode:
            print(f"NOT RUN: required local image {image} is unavailable")
            return 2
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="artifact-workflow-gate-") as temporary:
        directory = Path(temporary)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        environment = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(filter(None, (str(root), os.environ.get("PYTHONPATH")))),
            "PYTHONUNBUFFERED": "1",
            "ARTIFACT_RUNNER_DOCKER": "1",
            "ARTIFACT_WORKFLOW_GATE": "1",
            "ARTIFACT_DOCKER": docker,
            "ARTIFACT_RUNNER_STATE_PATH": str(directory / "broker"),
            "ARTIFACT_RUNNER_KEY": secrets.token_hex(32),
            "DB_HOST": url.host,
            "DB_PORT": str(url.port or 5432),
            "DB_NAME": url.database,
            "DB_USER": url.username,
            "DB_PASSWORD": url.password,
            "AUTH_SECRET_KEY": "offline-fixture-auth-secret-2026-" * 2,
            "MEMORY_EXTRACTION_ENABLED": "false",
            "LANGSMITH_TRACING_ENABLED": "false",
            "MCP_DEMO_ENABLED": "false",
            "V2_ENABLED": "false",
        }
        environment["ARTIFACT_GATE_RUNNER_URL"] = f"http://127.0.0.1:{port}"
        environment["ARTIFACT_GATE_RUNNER_KEY"] = environment["ARTIFACT_RUNNER_KEY"]
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            environment["ARTIFACT_GATE_REPORT"] = str(args.report.resolve())
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "artifact_runtime.app:application_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ]
        logs = (directory / "broker.log").open("wb")

        def start():
            import httpx

            process = subprocess.Popen(command, cwd=root, env=environment, stdout=logs, stderr=logs)
            for _ in range(150):
                try:
                    if (
                        httpx.get(
                            environment["ARTIFACT_GATE_RUNNER_URL"] + "/health/live",
                            timeout=0.2,
                            trust_env=False,
                        ).status_code
                        == 200
                    ):
                        return process
                except httpx.HTTPError:
                    pass
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            process.kill()
            process.wait()
            raise RuntimeError("Task-owned broker did not become healthy")

        process = None
        try:
            process = start()
            request_id = uuid4()

            async def probe():
                from artifact_runtime.protocol import ArtifactOperation, ArtifactRunRequest
                from backend.services.artifacts.runner_client import ArtifactRunnerClient
                from backend.services.artifacts.storage import ArtifactStorage

                async def cancel():
                    pass

                client = ArtifactRunnerClient(
                    environment["ARTIFACT_GATE_RUNNER_URL"],
                    key=environment["ARTIFACT_RUNNER_KEY"],
                    storage=ArtifactStorage(directory / "probe"),
                    owner=request_id,
                )
                result = await client.execute(
                    ArtifactRunRequest(
                        job_id=request_id,
                        operation=ArtifactOperation(
                            kind="python",
                            code=(
                                "from openpyxl import Workbook; "
                                "Workbook().save('/output/output-0.xlsx')"
                            ),
                        ),
                    ),
                    (),
                    cancel=cancel,
                )
                return result.files[0].sha256

            original = asyncio.run(probe())
            process.kill()
            process.wait()
            process = start()
            if asyncio.run(probe()) != original:
                raise RuntimeError("Broker restart changed a completed output")
            print("PASS: hard broker restart replays the same completed binary output", flush=True)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-s",
                    "-p",
                    "no:cacheprovider",
                    f"--basetemp={directory}/pytest",
                    "tests/test_artifact_workflow.py",
                    "tests/test_artifact_api.py",
                    "tests/test_artifact_tools.py",
                    "tests/test_artifact_publication.py",
                    "tests/test_artifact_runner.py",
                    "tests/test_artifact_storage.py",
                    "tests/test_artifact_versions.py",
                ],
                cwd=root,
                env=environment,
                check=False,
            )
            print("Live model: NOT RUN (offline fixture only)")
            if result.returncode == 0:
                print(
                    "PASS: offline artifact workflow, real queue/worker/checkpoints/runner/database"
                )
            return result.returncode
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            logs.close()


if __name__ == "__main__":
    raise SystemExit(main())
