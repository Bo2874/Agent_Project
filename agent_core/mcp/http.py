"""Streamable HTTP adapter using the SDK transport over guarded outbound networking."""

import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from agent_core.mcp.client import MCPClientAdapter, MCPStdioConfig
from agent_core.mcp.outbound import OutboundDenied, OutboundPolicy, PinnedTransport


class _MCPTransport(PinnedTransport):
    def __init__(self, config, policy):
        super().__init__(policy, ssl_context=config.ssl_context)
        self.authorization = config.authorization

    async def handle_async_request(self, request):
        try:
            self.policy.validate_url(str(request.url))
        except OutboundDenied:
            return httpx2.Response(403, json={"error": "endpoint_blocked"})
        if self.authorization:
            try:
                headers = await self.authorization()
                if set(key.lower() for key in headers) - {"authorization", "x-api-key"}:
                    raise ValueError("Unsupported credential header")
                request.headers.update(headers)
            except Exception:
                # A local denial must not escape into the SDK's transport task group.
                return httpx2.Response(401, json={"error": "connection_authorization_required"})
        try:
            return await super().handle_async_request(request)
        except OutboundDenied:
            return httpx2.Response(403, json={"error": "endpoint_blocked"})
        except Exception:
            return httpx2.Response(502, json={"error": "mcp_transport_unavailable"})


@dataclass(frozen=True, slots=True)
class MCPHTTPConfig(MCPStdioConfig):
    command: str = "@http"
    endpoint: str = ""
    network_policy: OutboundPolicy | None = field(default=None, repr=False)
    authorization: Callable[[], Awaitable[dict[str, str]]] | None = field(default=None, repr=False)
    owner_user_id: UUID | None = None
    ssl_context: ssl.SSLContext | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        super(MCPHTTPConfig, self).__post_init__()
        policy = self.network_policy or OutboundPolicy((self.endpoint,))
        policy.validate_url(self.endpoint)
        if not self.bindings or (self.authorization and self.owner_user_id is None):
            raise ValueError("HTTP requires host bindings and owner-scoped authorization")


class MCPHTTPAdapter(MCPClientAdapter):
    config: MCPHTTPConfig

    async def _open_client(self, stack):
        config = self.config
        policy = config.network_policy or OutboundPolicy((config.endpoint,))

        http = await stack.enter_async_context(
            httpx2.AsyncClient(
                transport=_MCPTransport(config, policy),
                trust_env=False,
                follow_redirects=False,
                timeout=httpx2.Timeout(15),
            )
        )
        transport = streamable_http_client(config.endpoint, http_client=http)
        return await stack.enter_async_context(
            Client(
                transport,
                mode=config.protocol_mode,
                cache=None,
                read_timeout_seconds=config.startup_timeout_seconds,
            )
        )

    def _context_allowed(self, context) -> bool:
        return self.config.owner_user_id is None or (
            context is not None and context.user_id == self.config.owner_user_id
        )
