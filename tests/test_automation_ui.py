import asyncio
import os
import shutil
import socket
import subprocess
import sys

import httpx
from google_fixtures import google_db  # noqa: F401


def test_automation_controls_in_real_browser():
    result = subprocess.run(
        [shutil.which("node"), "tests/automations_browser.cjs"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_automation_page_with_real_http_database_and_worker(google_db, tmp_path):  # noqa: F811
    db = google_db
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "AUTOMATION_QA_SCHEMA": db.schema,
        "AUTOMATION_QA_OWNER": str(db.owner),
        "AUTOMATION_QA_PORT": str(port),
        "PYTHONUTF8": "1",
    }
    log = tmp_path / "web.log"
    with log.open("wb") as output:
        process = subprocess.Popen(
            [sys.executable, "tests/automation_web_fixture.py"],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            async with httpx.AsyncClient() as client:
                for _ in range(150):
                    if process.poll() is not None:
                        raise AssertionError(log.read_text(encoding="utf-8"))
                    try:
                        response = await client.get(base + "/api/automations", timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError(
                        "Browser fixture did not start: " + log.read_text(encoding="utf-8")
                    )
            result = await asyncio.to_thread(
                subprocess.run,
                [shutil.which("node"), "tests/automations_page_browser.cjs"],
                env={**env, "AUTOMATION_UI_URL": base},
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            assert result.returncode == 0, (
                result.stdout + result.stderr + log.read_text(encoding="utf-8")[-4000:]
            )
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=10)
