"""Capture one baseline command; logs are redacted and each gate has its own result."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.core.redaction import redact_text  # noqa: E402


def main() -> int:
    name, *command = sys.argv[1:]
    if not re.fullmatch(r"[a-z0-9_-]+", name) or not command:
        raise SystemExit("Usage: run_gate.py GATE COMMAND [ARGS ...]")
    started = datetime.now(UTC).isoformat()
    before = time.perf_counter()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, timeout=600, check=False
        )
        output = result.stdout.decode("utf-8", errors="replace")
        output += result.stderr.decode("utf-8", errors="replace")
        code = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, output = 124, str(exc)
    elapsed = round(time.perf_counter() - before, 3)
    # Never publish configured credential values from an exception or subprocess log.
    from dotenv import dotenv_values

    values = {**dotenv_values(ROOT / ".env"), **os.environ}
    for key, value in values.items():
        if (
            value
            and len(value) >= 8
            and re.search(r"password|secret|api.?key|access.?token|database.?url", key, re.I)
        ):
            output = output.replace(value, "[REDACTED]")
    output = re.sub(
        r"(?i)(?:postgresql|postgres|redis)(?:\+[a-z0-9]+)?://\S+", "[REDACTED_DB_URL]", output
    )
    output = redact_text(output)
    directory = Path(__file__).resolve().parent
    (directory / f"{name}.log").write_text(output, encoding="utf-8")
    payload = {
        "gate": name,
        "command": command,
        "started_at_utc": started,
        "duration_seconds": elapsed,
        "exit_code": code,
        "status": "passed" if code == 0 else "failed",
        "log": f"{name}.log",
    }
    (directory / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False))
    print(output[-18000:])
    return code


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
