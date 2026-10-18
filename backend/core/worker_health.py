"""Small per-container heartbeat, updated by the actual worker execution loop."""

import json
import math
import os
import time
from pathlib import Path


def write_worker_heartbeat(path: Path, worker_id: str, queue_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "worker_id": worker_id,
                "queue_name": queue_name,
                "pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )
    pending.replace(path)


def worker_heartbeat_is_fresh(path: Path, queue_name: str, max_age: float) -> bool:
    try:
        if path.stat().st_size > 4096:
            return False
        state = json.loads(path.read_text(encoding="utf-8"))
        updated_at = state["updated_at"]
        if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
            return False
        return (
            math.isfinite(updated_at)
            and state.get("queue_name") == queue_name
            and 0 <= time.time() - updated_at <= max_age
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
