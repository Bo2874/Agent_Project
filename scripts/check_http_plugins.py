"""Exercise the HTTP adapter against real local TLS modern/legacy MCP fixtures."""

import asyncio
import json
from uuid import uuid4

from agent_core.mcp.client import MCPToolBinding
from agent_core.mcp.http import MCPHTTPConfig
from agent_core.mcp.outbound import OutboundDenied, outbound_client
from agent_core.plugins.manager import PluginManager
from agent_core.tools import ToolContext, ToolExecutionPolicy, ToolExecutor, ToolRegistry
from scripts.http_plugin_fixture import http_fixture


async def check():
    versions = {}
    for label, legacy, json_response in (
        ("modern_json", False, True),
        ("modern_sse", False, False),
        ("legacy_json", True, True),
        ("legacy_sse", True, False),
    ):
        async with http_fixture(legacy=legacy, json_response=json_response) as fixture:
            config = MCPHTTPConfig(
                name="http__fixture",
                endpoint=fixture.base + "/mcp",
                expected_release="0.1.0",
                network_policy=fixture.policy("/mcp"),
                ssl_context=fixture.ssl_context,
                bindings=(MCPToolBinding("echo", "http__fixture__echo", ToolExecutionPolicy()),),
            )
            registry = ToolRegistry()
            manager = PluginManager(registry, (config,))
            try:
                (health,) = await manager.start()
                assert health.status == "healthy", f"{label}: {health.error_code}"
                versions[label] = health.protocol_version
                result = await ToolExecutor().execute(
                    registry,
                    ToolContext(user_id=uuid4(), conversation_id=uuid4(), user_role="user"),
                    "http__fixture__echo",
                    {"value": label},
                )
                assert not result.is_error and result.data["value"] == label
                assert "fixture.test" in fixture.sni
                catalog_version = registry.capability_catalog("user").version
                await manager.refresh_discovery(config.name)
                assert registry.capability_catalog("user").version == catalog_version
            finally:
                await manager.close()
            assert len(registry) == 0
            try:
                async with outbound_client(
                    fixture.policy("/redirect"), ssl_context=fixture.ssl_context
                ) as client:
                    await client.get(fixture.base + "/redirect")
            except OutboundDenied:
                pass
            else:
                raise AssertionError("HTTP redirect was followed")
    assert versions["modern_json"] == versions["modern_sse"] == "2026-07-28"
    assert versions["legacy_json"] == versions["legacy_sse"] == "2025-11-25"
    async with http_fixture(require_auth=True) as fixture:
        owner_id, revoked = uuid4(), False
        fixture.access.add("fixture-token")

        async def authorization():
            if revoked:
                raise ValueError("private-denial-canary")
            return {"authorization": "Bearer fixture-token"}

        config = MCPHTTPConfig(
            name="private__fixture",
            endpoint=fixture.base + "/mcp",
            expected_release="0.1.0",
            network_policy=fixture.policy("/mcp"),
            ssl_context=fixture.ssl_context,
            owner_user_id=owner_id,
            authorization=authorization,
            bindings=(MCPToolBinding("echo", "private__fixture__echo", ToolExecutionPolicy()),),
        )
        registry = ToolRegistry()
        manager = PluginManager(registry, (config,))
        try:
            (health,) = await manager.start()
            assert health.status == "healthy"
            revoked = True
            before = fixture.stats["requests"]
            result = await ToolExecutor().execute(
                registry,
                ToolContext(
                    user_id=owner_id,
                    conversation_id=uuid4(),
                    user_role="user",
                ),
                "private__fixture__echo",
                {"value": "denied"},
            )
            assert result.is_error and fixture.stats["requests"] == before
            assert "private-denial-canary" not in result.content
        finally:
            await manager.close()
        assert len(registry) == 0
    return {
        "protocol_matrix": versions,
        "json_and_sse": True,
        "tls_sni_verified": True,
        "redirect_blocked": True,
        "registry_cleanup": True,
        "revocation_cleanup_safe": True,
        "http_discovery_noop_stable": True,
        "llm_provider_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check())))
