"""Offline stdio fixture used only by PluginManager regression probes."""

import argparse
import asyncio
import os
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--catalog-file", type=Path)
    args = parser.parse_args()
    if args.silent:
        time.sleep(60)
        return

    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    class FixtureServer(MCPServer):
        async def list_tools(self):
            tools = await super().list_tools()
            if args.catalog_file:
                mode = args.catalog_file.read_text(encoding="utf-8").strip()
                if mode == "invalid":
                    tools[0].input_schema = {"type": "array"}
                elif mode == "changed":
                    for tool in tools:
                        if tool.name == "echo":
                            tool.input_schema = {
                                "type": "object",
                                "properties": {"value": {"type": "integer"}},
                                "required": ["value"],
                            }
                elif mode == "removed":
                    tools = [tool for tool in tools if tool.name != "echo"]
            return tools

    server = FixtureServer("plugin-manager-fixture", version="0.1.0")

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            idempotent_hint=True,
            destructive_hint=False,
        )
    )
    async def echo(value: str, delay: float = 0) -> dict[str, str | int]:
        """Echo synthetic text with an optional delay."""
        await asyncio.sleep(max(0, min(delay, 2)))
        return {"value": value, "pid": os.getpid()}

    @server.tool()
    async def crash() -> dict[str, str]:
        """Exit this synthetic child process to test transport isolation."""
        os._exit(17)

    server.run("stdio")


if __name__ == "__main__":
    main()
