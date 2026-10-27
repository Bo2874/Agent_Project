"""Run the durable PostgreSQL background worker as a separate process."""

import argparse
import asyncio
import os
import re
import signal
import socket
import sys

from backend.background import (
    BackgroundWorker,
    PostgresJobQueue,
    build_background_job_registry,
)
from backend.core.config import get_settings
from backend.core.logging import install_log_redaction
from backend.core.worker_health import write_worker_heartbeat
from backend.database import engine
from backend.dependencies_tools import close_tool_connections, start_tool_connections
from backend.services.document_indexing import close_embedding_client
from backend.services.document_query import close_reranker_client
from backend.services.langgraph_checkpoints import (
    close_langgraph_checkpoints,
    start_langgraph_checkpoints,
)


def default_worker_id() -> str:
    raw_id = f"{socket.gethostname()}:{os.getpid()}"
    return re.sub(r"[^A-Za-z0-9_.:-]", "-", raw_id)[:100]


async def run(*, once: bool) -> None:
    settings = get_settings()
    worker_id = default_worker_id()
    heartbeat_path = settings.background_worker_heartbeat_path

    def heartbeat() -> None:
        if heartbeat_path is not None:
            write_worker_heartbeat(heartbeat_path, worker_id, settings.background_queue_name)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows keeps the existing KeyboardInterrupt behavior.
    worker = BackgroundWorker(
        queue=PostgresJobQueue(),
        registry=build_background_job_registry(),
        queue_name=settings.background_queue_name,
        worker_id=worker_id,
        poll_seconds=settings.background_worker_poll_seconds,
        lease_seconds=settings.background_job_lease_seconds,
        control_poll_seconds=settings.background_job_control_poll_seconds,
        retry_base_seconds=settings.background_job_retry_base_seconds,
        retry_max_seconds=settings.background_job_retry_max_seconds,
        heartbeat_handler=heartbeat,
    )
    try:
        await start_langgraph_checkpoints()
        await start_tool_connections()
        if once:
            processed = await worker.run_once()
            print("Đã xử lý một job." if processed else "Queue hiện không có job sẵn sàng.")
        else:
            print(
                f"Background worker {worker.worker_id} đang nghe queue "
                f"'{worker.queue_name}'. Nhấn Ctrl+C để dừng."
            )
            await worker.run_forever(stop)
    finally:
        await close_tool_connections()
        await close_langgraph_checkpoints()
        await close_reranker_client()
        await close_embedding_client()
        if heartbeat_path is not None:
            heartbeat_path.unlink(missing_ok=True)
        await engine.dispose()


def main() -> None:
    install_log_redaction()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Chạy PostgreSQL background worker.")
    parser.add_argument("--once", action="store_true", help="Xử lý tối đa một job rồi thoát.")
    args = parser.parse_args()
    try:
        asyncio.run(run(once=args.once))
    except KeyboardInterrupt:
        print("Background worker đã dừng.")


if __name__ == "__main__":
    main()
