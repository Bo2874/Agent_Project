"""Container readiness probes. Failure output contains no configuration secrets."""

import argparse
import os
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from backend.core.worker_health import worker_heartbeat_is_fresh


def main() -> None:
    parser = argparse.ArgumentParser(description="Check an app or worker container.")
    parser.add_argument("service", choices=("app", "worker"))
    service = parser.parse_args().service
    try:
        if service == "app":
            with build_opener(ProxyHandler({})).open(
                "http://127.0.0.1:8000/health/ready", timeout=3
            ) as response:
                if response.status != 200:
                    raise RuntimeError("App is not ready")
        else:
            path = os.getenv("BACKGROUND_WORKER_HEARTBEAT_PATH", "")
            if not path or not worker_heartbeat_is_fresh(
                Path(path),
                os.getenv("BACKGROUND_QUEUE_NAME", "default"),
                float(os.getenv("BACKGROUND_WORKER_HEARTBEAT_MAX_AGE_SECONDS", "15")),
            ):
                raise RuntimeError("Worker heartbeat is missing or stale")
    except Exception as exc:
        print(f"{service} healthcheck failed: {type(exc).__name__}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
