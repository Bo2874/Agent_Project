from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.core.config import get_settings


class AsyncPostgresSaverAdapter(BaseCheckpointSaver[Any]):
    """Expose the sync PostgreSQL saver without blocking FastAPI's event loop."""

    def __init__(self, saver: PostgresSaver) -> None:
        super().__init__(serde=saver.serde)
        self._saver = saver

    async def aget_tuple(self, config: Any) -> Any:
        return await asyncio.to_thread(self._saver.get_tuple, config)

    async def alist(
        self,
        config: Any | None,
        *,
        filter: dict[str, Any] | None = None,
        before: Any | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Any]:
        items = await asyncio.to_thread(
            lambda: list(
                self._saver.list(
                    config,
                    filter=filter,
                    before=before,
                    limit=limit,
                )
            )
        )
        for item in items:
            yield item

    async def aput(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> Any:
        return await asyncio.to_thread(
            self._saver.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: Any,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(
            self._saver.put_writes,
            config,
            writes,
            task_id,
            task_path,
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self._saver.delete_thread, thread_id)


class LangGraphCheckpointService:
    """Own the PostgreSQL pool and saver used by all production agent graphs."""

    def __init__(
        self,
        *,
        conninfo: str,
        min_size: int,
        max_size: int,
        setup_schema: bool = True,
    ) -> None:
        self._conninfo = conninfo
        self._min_size = min_size
        self._max_size = max_size
        self._setup_schema = setup_schema
        self._pool: ConnectionPool | None = None
        self._saver: AsyncPostgresSaverAdapter | None = None

    @property
    def saver(self) -> AsyncPostgresSaverAdapter:
        if self._saver is None:
            raise RuntimeError("LangGraph PostgreSQL checkpointer chưa được khởi động")
        return self._saver

    async def start(self) -> None:
        if self._pool is not None:
            return
        pool = ConnectionPool(
            self._conninfo,
            min_size=self._min_size,
            max_size=self._max_size,
            open=False,
            name="langgraph-checkpoints",
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        try:
            await asyncio.to_thread(pool.open, True)
            postgres_saver = PostgresSaver(
                pool,
                serde=JsonPlusSerializer(allowed_msgpack_modules=None),
            )
            if self._setup_schema:
                await asyncio.to_thread(postgres_saver.setup)
            saver = AsyncPostgresSaverAdapter(postgres_saver)
        except BaseException:
            await asyncio.to_thread(pool.close)
            raise
        self._pool = pool
        self._saver = saver

    async def close(self) -> None:
        pool = self._pool
        self._pool = None
        self._saver = None
        if pool is not None:
            await asyncio.to_thread(pool.close)


_checkpoint_service: LangGraphCheckpointService | None = None


async def start_langgraph_checkpoints() -> None:
    global _checkpoint_service
    if _checkpoint_service is not None:
        return
    settings = get_settings()
    service = LangGraphCheckpointService(
        conninfo=settings.langgraph_checkpoint_database_uri,
        min_size=settings.langgraph_checkpoint_pool_min_size,
        max_size=settings.langgraph_checkpoint_pool_max_size,
        setup_schema=settings.langgraph_checkpoint_auto_setup,
    )
    await service.start()
    _checkpoint_service = service


def get_langgraph_checkpointer() -> AsyncPostgresSaverAdapter:
    if _checkpoint_service is None:
        raise RuntimeError("LangGraph PostgreSQL checkpointer chưa được khởi động")
    return _checkpoint_service.saver


async def close_langgraph_checkpoints() -> None:
    global _checkpoint_service
    service = _checkpoint_service
    _checkpoint_service = None
    if service is not None:
        await service.close()
