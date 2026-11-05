"""Crash recovery must reclaim only stale, unlocked, recognizable scratch data."""

import asyncio
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


def killed_workspace(root, prefix):
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            """
import sys, time
from pathlib import Path
from artifact_runtime.temporary import temporary_workspace
with temporary_workspace(Path(sys.argv[1]), sys.argv[2]) as folder:
    (folder / 'output-0.xlsx').write_bytes(b'private crash fixture')
    print(str(folder), flush=True)
    time.sleep(120)
""",
            str(root),
            prefix,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = process.stdout.readline().strip()
        if not line:
            raise AssertionError(process.stderr.read())
        folder = Path(line)
    finally:
        process.kill()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
    return folder


@pytest.mark.parametrize("prefix", ["artifact-stage-", "artifact-upload-", "artifact-validated-"])
def test_hard_kill_scratch_recovery_retains_active_recent_and_unknown(tmp_path, prefix):
    from artifact_runtime.temporary import reap_workspaces, temporary_workspace

    root = tmp_path / "private"
    root.mkdir()
    stale = killed_workspace(root, prefix)
    old = time.time() - 90000
    os.utime(stale, (old, old))
    with temporary_workspace(root, prefix) as active:
        (active / "output-0.xlsx").write_bytes(b"active")
        os.utime(active, (old, old))
        recent = killed_workspace(root, prefix)
        unknown = root / "unknown"
        unknown.mkdir()
        (unknown / "output-0.xlsx").write_bytes(b"keep")
        assert reap_workspaces(root, prefix, grace_seconds=86400, dry_run=True) == 1
        assert stale.exists()
        assert reap_workspaces(root, prefix, grace_seconds=86400) == 1
        assert not stale.exists()
        assert (active / "output-0.xlsx").read_bytes() == b"active"
        assert recent.exists() and unknown.exists()
    assert not active.exists()


def test_scratch_cleanup_refuses_unexpected_nested_content(tmp_path):
    from artifact_runtime.temporary import reap_workspaces

    folder = killed_workspace(tmp_path, "artifact-stage-")
    (folder / "unexpected").mkdir()
    (folder / "unexpected" / "keep.txt").write_text("keep")
    old = time.time() - 90000
    os.utime(folder, (old, old))
    assert reap_workspaces(tmp_path, "artifact-stage-", grace_seconds=86400) == 0
    assert (folder / "unexpected" / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("failure", [OSError, sqlite3.OperationalError])
async def test_reaper_retries_after_transient_failure(tmp_path, monkeypatch, caplog, failure):
    from artifact_runtime import app as module

    resumed = asyncio.Event()

    class FlakyBroker:
        async def recover(self):
            pass

        async def reap_stale(self):
            self.calls += 1
            if self.calls == 1:
                raise failure("transient fixture failure")
            resumed.set()

        calls = 0

    real_sleep = asyncio.sleep

    async def quick_sweep(delay):
        await real_sleep(0 if delay == 30 else delay)

    monkeypatch.setattr(module.asyncio, "sleep", quick_sweep)
    broker = FlakyBroker()
    app = module.create_app(broker, key="k" * 32)
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(resumed.wait(), timeout=0.5)
    assert broker.calls >= 2
    assert "transient fixture failure" in caplog.text
