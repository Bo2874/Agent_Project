"""Automation acceptance. Offline uses an isolated test DB; live runs one selected draft."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4


async def live(identity, timeout):
    from backend.core.config import get_settings
    from backend.database import SessionFactory, engine
    from backend.models.automation import Automation, AutomationRun
    from backend.services.automations.service import run_now

    if not get_settings().automations_enabled:
        print("NOT RUN: AUTOMATIONS_ENABLED is false")
        return 2
    try:
        async with SessionFactory() as session:
            auto = await session.get(Automation, identity)
            if auto is None:
                print("NOT RUN: selected automation not found")
                return 2
            run = await run_now(
                session, auto.owner_id, identity, idempotency_key=f"acceptance-{uuid4()}"
            )
            run_id = run.id
        print(json.dumps({"automation_id": str(identity), "run_id": str(run_id)}), flush=True)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            async with SessionFactory() as session:
                row = await session.get(AutomationRun, run_id)
                status, result = row.status, row.result
            if status not in {"queued", "running", "cancelling"}:
                print(json.dumps({"status": status, "total_tokens": result.get("total_tokens")}))
                # A successful model run still requires the operator to inspect the target file.
                return 0 if status == "succeeded" else 2
            if asyncio.get_running_loop().time() >= deadline:
                print(
                    "PENDING: monitor timeout; run continues. "
                    "Inspect Automations/Inbox or stop it there."
                )
                return 2
            await asyncio.sleep(1)
    finally:
        await engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--automation-id", type=UUID)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.live:
        if args.automation_id is None:
            parser.error("--live requires --automation-id for the explicitly selected test draft")
        if args.timeout <= 0:
            parser.error("--timeout must be positive")
        return asyncio.run(live(args.automation_id, args.timeout))
    from sqlalchemy.engine import make_url

    raw = os.environ.get("GOOGLE_TEST_DATABASE_URL")
    if not raw or not (make_url(raw).database or "").endswith("_test"):
        print("NOT RUN: provide task-owned GOOGLE_TEST_DATABASE_URL ending in _test")
        return 2
    root = Path(__file__).resolve().parents[1]
    tests = sorted(str(p.relative_to(root)) for p in (root / "tests").glob("test_automation*.py"))
    tests += [
        "tests/test_agent_budgeting.py",
        "tests/test_agent_jobs.py",
        "tests/test_task_workers.py",
    ]
    command = [sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        command.append("--junitxml=" + str(args.report.resolve()))
    environment = {
        **os.environ,
        "DB_PASSWORD": "offline-automation-fixture",
        "AUTH_SECRET_KEY": "offline-automation-fixture-" * 2,
        "LANGSMITH_TRACING_ENABLED": "false",
    }
    for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        environment.pop(key, None)
    result = subprocess.run(command, cwd=root, env=environment, check=False)
    print("Live Google and paid models: NOT RUN", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
