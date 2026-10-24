"""Offline catalog scale/token probe and real MCP discovery between run snapshots."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from agent_core.context_budget import estimate_tokens
from agent_core.mcp import MCPStdioConfig
from agent_core.plugins.manager import PluginManager
from agent_core.tools import (
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolSelectionConfig,
    ToolSelector,
    create_context_tool,
)


async def check_discovery(directory: Path) -> dict:
    control = directory / "catalog-mode.txt"
    control.write_text("original", encoding="utf-8")
    config = MCPStdioConfig(
        name="changing",
        command=sys.executable,
        args=("-m", "scripts.mcp_stdio_fixture", "--catalog-file", str(control)),
        health_interval_seconds=600,
    )
    stable = MCPStdioConfig(
        name="stable",
        command=sys.executable,
        args=("-m", "scripts.mcp_stdio_fixture"),
        health_interval_seconds=600,
    )
    registry = ToolRegistry()
    manager = PluginManager(registry, (config, stable))
    context = ToolContext(uuid4(), uuid4(), "user", uuid4())
    selector = ToolSelector()
    try:
        await manager.start()
        assert all(h.status == "healthy" for h in manager.health())
        old = selector.select(registry, "user", "echo")
        executor = ToolExecutor(expected_tools={t.name: registry.get(t.name) for t in old.tools})
        first = await executor.execute(registry, context, "changing__echo", {"value": "old"})
        assert not first.is_error
        await manager.refresh_discovery("changing")
        assert registry.capability_catalog("user").version == old.catalog_version
        control.write_text("changed", encoding="utf-8")
        await manager.refresh_discovery("changing")
        new = selector.select(registry, "user", "echo")
        assert new.catalog_version != old.catalog_version
        old_schema = next(d for d in old.definitions if d["function"]["name"] == "changing__echo")
        new_schema = next(d for d in new.definitions if d["function"]["name"] == "changing__echo")
        assert old_schema["function"]["parameters"]["properties"]["value"]["type"] == "string"
        assert new_schema["function"]["parameters"]["properties"]["value"]["type"] == "integer"
        # Same idempotent call must not bypass the pinned generation through memoization.
        stale = await executor.execute(registry, context, "changing__echo", {"value": "old"})
        assert stale.error_code == "tool_catalog_changed"
        stable_result = await ToolExecutor().execute(
            registry, context, "stable__echo", {"value": "stable"}
        )
        assert not stable_result.is_error
        control.write_text("removed", encoding="utf-8")
        await manager.refresh_discovery("changing")
        assert "changing__echo" not in selector.select(registry, "user", "echo").loaded_names
        control.write_text("invalid", encoding="utf-8")
        try:
            await manager.refresh_discovery("changing")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Invalid remote schema was published")
        assert not any(t.name.startswith("changing__") for t in registry.list_tools())
        assert "stable__echo" in registry
        return {
            "no_op_stable": True,
            "new_run_sees_schema_change": True,
            "old_run_pinned": True,
            "cached_result_generation_guard": True,
            "removed_tool_disappears": True,
            "invalid_schema_isolated": True,
        }
    finally:
        await manager.close()
        assert len(registry) == 0


def check_scale() -> dict:
    registry = ToolRegistry()

    async def handler(_, arguments):
        return ToolResult.success("synthetic")

    for i in range(200):
        registry.register(
            create_context_tool(
                name=f"tool_{i}",
                description=f"Search topic{i}",
                args_schema={
                    "type": "object",
                    "properties": {
                        "q": {
                            "type": "string",
                            "description": "synthetic schema documentation " * 50,
                        }
                    },
                },
                coroutine=handler,
            )
        )
    catalog = registry.capability_catalog("user")
    assert not registry._schemas
    selection = ToolSelector(ToolSelectionConfig(schema_token_budget=800)).select(
        registry, "user", "topic42"
    )
    assert selection.loaded_names == {"tool_42"} and len(registry._schemas) == 1
    eager = [registry.model_definition(tool.name) for tool in registry.list_tools()]
    eager_tokens = estimate_tokens(eager)
    assert 0 < selection.schema_tokens <= 800 < eager_tokens
    return {
        "metadata_count": len(catalog.records),
        "converted_schemas_before_selection": 0,
        "converted_schemas_for_query": 1,
        "loaded_names": sorted(selection.loaded_names),
        "schema_token_budget": 800,
        "selected_schema_tokens": selection.schema_tokens,
        "eager_schema_tokens": eager_tokens,
        "estimator": "utf8_bytes_v1",
        "reduction_percent": round(100 * (1 - selection.schema_tokens / eager_tokens), 2),
    }


async def check() -> dict:
    with tempfile.TemporaryDirectory(prefix="capability-discovery-") as directory:
        return {
            "scale": check_scale(),
            "discovery": await check_discovery(Path(directory)),
            "llm_provider_calls": 0,
        }


def main():
    print(json.dumps(asyncio.run(check()), indent=2))


if __name__ == "__main__":
    main()
