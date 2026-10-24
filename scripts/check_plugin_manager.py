"""Prove stdio failure isolation with real, keyless child processes (no DB required)."""

import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from agent_core.mcp import MCPStdioConfig
from agent_core.mcp.client import MCPToolBinding
from agent_core.plugins.manager import PluginManager
from agent_core.tools import ToolContext, ToolExecutionPolicy, ToolExecutor, ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


def process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def check() -> dict:
    registry = ToolRegistry()
    template = MCPStdioConfig(
        name="fixture__one",
        command=sys.executable,
        args=("-m", "scripts.mcp_stdio_fixture"),
        cwd=str(ROOT),
        expected_release="0.1.0",
        startup_timeout_seconds=20,
        request_timeout_seconds=2,
        health_interval_seconds=0.1,
        health_timeout_seconds=0.5,
        failure_threshold=1,
        restart_cooldown_seconds=0.05,
    )

    def server(name):
        return replace(
            template,
            name=name,
            plugin_id=name,
            request_timeout_seconds=0.25 if name == "fixture__one" else 2,
            bindings=(
                MCPToolBinding("echo", f"{name}__echo", ToolExecutionPolicy(timeout_seconds=3)),
                MCPToolBinding(
                    "crash",
                    f"{name}__crash",
                    ToolExecutionPolicy(
                        timeout_seconds=3,
                        allowed_roles=frozenset({"admin"}),
                        requires_approval=True,
                    ),
                ),
            ),
        )

    manager = PluginManager(
        registry,
        (
            server("fixture__one"),
            server("fixture__two"),
            replace(template, name="broken", args=("-c", "raise SystemExit(17)")),
            replace(
                template,
                name="silent",
                args=("-c", "import time; time.sleep(60)"),
                startup_timeout_seconds=0.1,
            ),
        ),
    )
    context = ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user")
    executor = ToolExecutor()
    pids = set()

    async def echo(name, value, delay=0):
        result = await executor.execute(
            registry,
            context,
            f"{name}__echo",
            {"value": value, "delay": delay},
        )
        if not result.is_error:
            pids.add(result.data["pid"])
        return result

    async def opened(name):
        async with asyncio.timeout(5):
            while (
                next(row for row in manager.health() if row.name == name).status != "circuit_open"
            ):
                await asyncio.sleep(0.02)

    try:
        await manager.start()
        states = {row.name: row for row in manager.health()}
        assert states["fixture__one"].status == states["fixture__two"].status == "healthy"
        assert states["broken"].status == states["silent"].status == "circuit_open"
        assert manager.health_payload()["degraded"]
        assert len(registry) == 4
        await manager.start()  # Idempotent; no reconnect storm for broken entries.
        assert not (await echo("fixture__one", "first")).is_error
        assert not (await echo("fixture__two", "second")).is_error
        policy = registry.get("fixture__one__echo").policy
        assert policy.idempotent is False  # Remote annotation claims true; host policy wins.
        denied = await executor.execute(registry, context, "fixture__one__crash", {})
        assert denied.error_code == "tool_permission_denied"
        stale_tool = registry.get("fixture__one__echo").tool
        slow = asyncio.create_task(echo("fixture__one", "slow", 1.5))
        await asyncio.sleep(0.03)
        assert not (await echo("fixture__two", "while-first-busy")).is_error
        assert not slow.done()
        timed_out = await slow
        assert timed_out.is_error and timed_out.error_code == "tool_timeout", (
            f"Synthetic slow call returned error={timed_out.is_error}, code={timed_out.error_code}"
        )
        await opened("fixture__one")
        assert "fixture__one__echo" not in registry and "fixture__two__echo" in registry
        stale = await stale_tool.ainvoke(
            {"value": "stale"},
            config={"configurable": {"tool_context": context}},
        )
        assert stale.is_error and stale.error_code == "mcp_not_connected"
        await asyncio.sleep(0.1)
        assert (await manager.restart("fixture__one")).status == "healthy"
        assert not (await echo("fixture__one", "after-restart")).is_error
        admin = replace(context, user_role="admin")
        crashed = await executor.execute(
            registry,
            admin,
            "fixture__one__crash",
            {},
            approval_granted=True,
        )
        assert crashed.is_error
        await opened("fixture__one")
        assert not (await echo("fixture__two", "after-other-process-exit")).is_error
    finally:
        await manager.close()
    assert len(registry) == 0
    deadline = time.monotonic() + 2
    while any(process_alive(pid) for pid in pids) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert all(not process_alive(pid) for pid in pids)
    assert not any(task.get_name().startswith("mcp-owner:") for task in asyncio.all_tasks())
    return {
        "healthy_stdio_servers": 2,
        "failed_startups_isolated": 2,
        "request_timeout_isolated": True,
        "process_exit_isolated": True,
        "host_policy_over_remote_annotations": True,
        "explicit_restart_recovered": True,
        "stale_handler_blocked": True,
        "registry_cleanup": True,
        "child_processes_reaped": True,
        "owner_tasks_cleaned": True,
        "llm_provider_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check())))
