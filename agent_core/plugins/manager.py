"""Independent stdio lifecycles. Each SDK context is entered/exited by one owner task."""

import asyncio
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from agent_core.mcp import MCPClientAdapter, MCPStdioConfig
from agent_core.mcp.http import MCPHTTPAdapter, MCPHTTPConfig
from agent_core.tools import ToolRegistry


@dataclass(frozen=True, slots=True)
class ServerHealth:
    name: str
    status: str
    failures: int
    error_code: str | None
    tool_names: tuple[str, ...]
    protocol_version: str | None
    checked_at: str | None


@dataclass(slots=True)
class _Server:
    config: MCPStdioConfig
    status: str = "stopped"
    failures: int = 0
    error_code: str | None = None
    checked_at: str | None = None
    opened_at: float = 0
    adapter: MCPClientAdapter | None = None
    task: asyncio.Task | None = None
    ready: asyncio.Future | None = None
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    tools: tuple[str, ...] = ()


class PluginManager:
    """Host-owned servers; never resolves user credentials or grants from a global registry."""

    def __init__(self, registry: ToolRegistry, configs: tuple[MCPStdioConfig, ...] = ()) -> None:
        self.registry = registry
        self._servers: dict[str, _Server] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._plugin_limits: dict[str, tuple[int, asyncio.Semaphore]] = {}
        for config in configs:
            self.add_server(config)

    def add_server(self, config: MCPStdioConfig) -> None:
        if config.name in self._servers or len(self._servers) >= 64:
            raise ValueError("Duplicate server name or server limit exceeded")
        key = config.plugin_id or config.name
        if (
            key in self._plugin_limits
            and self._plugin_limits[key][0] != config.max_plugin_concurrency
        ):
            raise ValueError("Servers of a plugin must share its concurrency limit")
        self._plugin_limits.setdefault(
            key, (config.max_plugin_concurrency, asyncio.Semaphore(config.max_plugin_concurrency))
        )
        self._servers[config.name] = _Server(config)

    def health(self) -> tuple[ServerHealth, ...]:
        return tuple(
            ServerHealth(
                name=name,
                status=row.status,
                failures=row.failures,
                error_code=row.error_code,
                tool_names=row.tools,
                protocol_version=row.adapter.protocol_version if row.adapter else None,
                checked_at=row.checked_at,
            )
            for name, row in sorted(self._servers.items())
        )

    def health_payload(self) -> dict:
        rows = self.health()
        return {
            "degraded": any(row.status in {"degraded", "circuit_open"} for row in rows),
            "servers": [asdict(row) for row in rows],
        }

    def _launch(self, row: _Server) -> None:
        row.stop = asyncio.Event()
        row.ready = asyncio.get_running_loop().create_future()
        row.status, row.error_code, row.failures = "starting", None, 0
        row.checked_at = None
        row.task = asyncio.create_task(self._run(row), name=f"mcp-owner:{row.config.name}")

    async def start(self) -> tuple[ServerHealth, ...]:
        async with self._lifecycle_lock:
            try:
                for row in self._servers.values():
                    if row.status == "stopped":
                        self._launch(row)
                await asyncio.gather(
                    *(asyncio.shield(row.ready) for row in self._servers.values() if row.ready)
                )
            except BaseException:
                await self._stop_all()
                raise
        return self.health()

    def _failure(self, row: _Server, code: str) -> None:
        if row.status not in {"starting", "healthy", "degraded"}:
            return
        row.failures += 1
        row.error_code, row.checked_at = code, datetime.now(UTC).isoformat()
        row.status = "degraded"
        if row.failures >= row.config.failure_threshold:
            self._open(row, code)

    def _open(self, row: _Server, code: str) -> None:
        row.status, row.error_code, row.opened_at = "circuit_open", code, time.monotonic()
        if row.adapter:
            row.adapter.detach()
        row.tools = ()
        row.stop.set()

    async def _run(self, row: _Server) -> None:
        adapter = row.adapter = (
            MCPHTTPAdapter(row.config)
            if isinstance(row.config, MCPHTTPConfig)
            else MCPClientAdapter(row.config)
        )
        adapter.plugin_semaphore = self._plugin_limits[row.config.plugin_id or row.config.name][1]
        adapter.on_failure = lambda code: self._failure(row, code)
        try:
            try:
                async with asyncio.timeout(row.config.startup_timeout_seconds):
                    tools = await adapter.connect_and_register(self.registry)
            except TimeoutError:
                self._open(row, "startup_timeout")
                return
            except Exception:
                self._open(row, "startup_failed")
                return
            row.tools, row.status = tuple(tool.name for tool in tools), "healthy"
            row.checked_at = datetime.now(UTC).isoformat()
            row.ready.set_result(None)
            while not row.stop.is_set():
                try:
                    await asyncio.wait_for(row.stop.wait(), row.config.health_interval_seconds)
                    break
                except TimeoutError:
                    pass
                alive = await adapter.healthcheck()
                row.checked_at = datetime.now(UTC).isoformat()
                if alive is False:
                    self._failure(row, "health_failed")
                elif alive is True and row.status != "circuit_open":
                    row.status, row.failures, row.error_code = "healthy", 0, None
        except asyncio.CancelledError:
            if not row.stop.is_set():
                self._open(row, "transport_closed")
            raise
        except Exception:
            self._open(row, "lifecycle_failed")
        finally:
            # This is the task that entered the SDK's AnyIO task groups/cancel scopes.
            try:
                await adapter.close()
            except Exception:
                self._open(row, "shutdown_failed")
            finally:
                row.tools, row.adapter = (), None
                if row.status != "circuit_open":
                    row.status = "stopped"
                if row.ready and not row.ready.done():
                    row.ready.set_result(None)

    async def _stop(self, row: _Server) -> None:
        task = row.task
        if task is None:
            return
        row.stop.set()
        starting = row.status == "starting"
        if row.status != "circuit_open":
            row.status = "stopping"
        if row.adapter:
            row.adapter.detach()
        if starting:
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=row.config.shutdown_timeout_seconds)
        if not done:
            task.cancel()
            # MCP SDK bounds process-tree termination; allow that cleanup to complete.
            done, _ = await asyncio.wait({task}, timeout=8)
            if not done:
                self._open(row, "shutdown_timeout")
                raise RuntimeError("MCP owner did not terminate within its shutdown budget")
        if not task.cancelled():
            task.exception()  # Retrieve owner errors; lifecycle status remains sanitized.
        row.task = None
        if row.error_code == "shutdown_failed":
            raise RuntimeError("MCP owner failed to shut down")

    async def stop(self, name: str) -> None:
        async with self._lifecycle_lock:
            row = self._servers[name]
            await self._stop(row)
            row.status = "stopped"

    async def restart(self, name: str) -> ServerHealth:
        async with self._lifecycle_lock:
            row = self._servers[name]
            if row.status == "circuit_open" and (
                time.monotonic() - row.opened_at < row.config.restart_cooldown_seconds
            ):
                raise RuntimeError("MCP restart cooldown has not elapsed")
            await self._stop(row)
            self._launch(row)
            try:
                await asyncio.shield(row.ready)
            except BaseException:
                await self._stop(row)
                raise
            return next(item for item in self.health() if item.name == name)

    async def refresh_discovery(self, name: str) -> dict:
        """Refresh a healthy host server; rejected discovery withdraws only this owner."""
        async with self._lifecycle_lock:
            row = self._servers[name]
            if row.adapter is None or row.status not in {"healthy", "degraded"}:
                raise RuntimeError("MCP server is not available for discovery")
            try:
                tools = await row.adapter.refresh_discovery()
                row.tools = tuple(tool.name for tool in tools)
                row.checked_at = datetime.now(UTC).isoformat()
                return {"server": name, "tool_names": row.tools, "status": "refreshed"}
            except Exception:
                self._open(row, "discovery_rejected")
                raise RuntimeError("MCP discovery failed; this server was withdrawn") from None

    async def _stop_all(self) -> None:
        results = await asyncio.gather(
            *(self._stop(row) for row in self._servers.values()), return_exceptions=True
        )
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("One or more MCP owners failed to shut down")

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_all()
