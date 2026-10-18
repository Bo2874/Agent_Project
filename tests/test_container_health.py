import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import ValidationError

from backend.background import BackgroundJobRegistry
from backend.core.config import Settings
from backend.core.worker_health import worker_heartbeat_is_fresh, write_worker_heartbeat
from backend.services import langgraph_checkpoints
from tests.test_background_jobs import FakeQueue, make_job, make_worker


def test_atomic_heartbeat_has_freshness_and_queue_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.core.worker_health.time.time", lambda: 100.0)
    path = tmp_path / "worker" / "heartbeat.json"
    write_worker_heartbeat(path, "worker-1", "default")
    assert worker_heartbeat_is_fresh(path, "default", 15)
    assert not path.with_suffix(".tmp").exists()
    assert not worker_heartbeat_is_fresh(path, "other", 15)
    monkeypatch.setattr("backend.core.worker_health.time.time", lambda: 116.0)
    assert not worker_heartbeat_is_fresh(path, "default", 15)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        "{}",
        '{"updated_at": true}',
        '{"updated_at": NaN,"queue_name":"default"}',
        '{"updated_at": 1000,"queue_name":"default"}',
        " " * 4097,
    ],
)
def test_missing_corrupt_and_future_heartbeat_is_unhealthy(tmp_path, monkeypatch, payload) -> None:
    monkeypatch.setattr("backend.core.worker_health.time.time", lambda: 100.0)
    path = tmp_path / "heartbeat.json"
    assert not worker_heartbeat_is_fresh(path, "default", 15)
    path.write_text(payload, encoding="utf-8")
    assert not worker_heartbeat_is_fresh(path, "default", 15)


async def test_worker_refreshes_heartbeat_while_a_job_is_running(tmp_path) -> None:
    path = tmp_path / "heartbeat.json"
    queue = FakeQueue(make_job())
    registry = BackgroundJobRegistry()
    writes = []

    def heartbeat() -> None:
        write_worker_heartbeat(path, "worker-1", "default")
        writes.append(json.loads(path.read_text())["updated_at"])

    async def handler(*_) -> dict:
        await asyncio.sleep(0.12)
        return {"ok": True}

    registry.register("test.echo", handler)
    worker = make_worker(queue, registry)
    worker.heartbeat_handler = heartbeat
    assert await worker.run_once()
    assert len(writes) >= 3
    assert worker_heartbeat_is_fresh(path, "default", 15)
    assert len(queue.succeeded) == 1


@pytest.mark.parametrize("setup_schema", [False, True])
async def test_checkpoint_setup_can_be_owned_only_by_migration(monkeypatch, setup_schema) -> None:
    calls = []
    pool = SimpleNamespace(open=lambda _: calls.append("open"), close=lambda: calls.append("close"))
    saver = SimpleNamespace(serde=JsonPlusSerializer(), setup=lambda: calls.append("setup"))
    monkeypatch.setattr(langgraph_checkpoints, "ConnectionPool", lambda *_, **__: pool)
    monkeypatch.setattr(langgraph_checkpoints, "PostgresSaver", lambda *_, **__: saver)
    service = langgraph_checkpoints.LangGraphCheckpointService(
        conninfo="test-only",
        min_size=1,
        max_size=1,
        setup_schema=setup_schema,
    )
    try:
        await service.start()
        assert calls.count("setup") == int(setup_schema)
    finally:
        await service.close()
    assert calls[-1] == "close"


def test_heartbeat_interval_must_not_exceed_health_max_age() -> None:
    with pytest.raises(ValidationError, match="heartbeat max age"):
        Settings(
            _env_file=None,
            db_password="test-password",
            auth_secret_key="x" * 32,
            background_worker_heartbeat_path=Path("test-heartbeat.json"),
            background_worker_poll_seconds=30,
            background_worker_heartbeat_max_age_seconds=15,
        )


def test_empty_heartbeat_env_value_keeps_host_worker_probe_disabled() -> None:
    settings = Settings(
        _env_file=None,
        db_password="test-password",
        auth_secret_key="x" * 32,
        background_worker_heartbeat_path="",
    )
    assert settings.background_worker_heartbeat_path is None
