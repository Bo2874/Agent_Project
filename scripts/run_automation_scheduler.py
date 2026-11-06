"""Run only the schedule dispatcher in a process independent of the web app."""

import argparse
import asyncio
import signal
from dataclasses import asdict
from datetime import UTC, datetime

from backend.core.config import get_settings
from backend.core.logging import install_log_redaction
from backend.core.worker_health import write_worker_heartbeat
from backend.database import SessionFactory, engine
from backend.services.automations.dispatch import dispatch_due
from scripts.run_background_worker import default_worker_id


async def run(*, once=False):
    settings = get_settings()
    if not settings.automations_enabled:
        raise RuntimeError("Set AUTOMATIONS_ENABLED=true to start the scheduler")
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    identity = default_worker_id()
    path = settings.automation_scheduler_heartbeat_path
    try:
        while not stop.is_set():
            async with SessionFactory() as session:
                report = await dispatch_due(session, now=datetime.now(UTC))
            if path:
                write_worker_heartbeat(path, identity, "automations")
            if once:
                return asdict(report)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        if path:
            path.unlink(missing_ok=True)
        await engine.dispose()


def main():
    install_log_redaction()
    parser = argparse.ArgumentParser(description="Dispatch scheduled automations")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        print(asyncio.run(run(once=args.once)) or "Scheduler stopped")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
