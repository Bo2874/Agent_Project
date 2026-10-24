"""Restricted outbound HTTP: approved URLs, public DNS answers and pinned TCP destinations."""

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpcore2
import httpx2


class OutboundDenied(ValueError):
    def __init__(self):
        super().__init__("Outbound endpoint blocked by host policy")


def canonical_url(value: str, *, allow_http: bool = False) -> str:
    try:
        if len(value) > 2048 or any(ord(c) <= 32 or c in "\\" for c in value):
            raise OutboundDenied()
        url = urlsplit(value)
        if url.scheme not in ({"https", "http"} if allow_http else {"https"}):
            raise OutboundDenied()
        if (
            not url.hostname
            or url.username is not None
            or url.password is not None
            or "?" in value
            or "#" in value
        ):
            raise OutboundDenied()
        host = url.hostname.encode("idna").decode("ascii").lower()
        if "%" in host or host.endswith(".") or len(host) > 253:
            raise OutboundDenied()
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise OutboundDenied()
        rendered = f"[{host}]" if ":" in host else host
        suffix = "" if port == (443 if url.scheme == "https" else 80) else f":{port}"
        return f"{url.scheme}://{rendered}{suffix}{url.path or '/'}"
    except (ValueError, UnicodeError):
        raise OutboundDenied() from None


async def resolve_dns(host: str, port: int) -> tuple[str, ...]:
    async with asyncio.timeout(5):
        answers = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    return tuple(dict.fromkeys(answer[4][0] for answer in answers))


@dataclass(frozen=True, slots=True)
class OutboundPolicy:
    urls: tuple[str, ...]
    # Only test harnesses inject exact fixture IPs; production profiles never expose these knobs.
    fixture_ips: frozenset[str] = field(default=frozenset(), repr=False)
    fixture_http: bool = False
    resolver: Callable[[str, int], Awaitable[tuple[str, ...]]] = field(
        default=resolve_dns,
        repr=False,
        compare=False,
    )

    def __post_init__(self):
        if not self.urls or len(self.urls) > 16:
            raise OutboundDenied()
        for url in self.urls:
            canonical_url(url, allow_http=self.fixture_http)

    def validate_url(self, value: str) -> str:
        canonical = canonical_url(value, allow_http=self.fixture_http)
        if canonical not in {canonical_url(url, allow_http=self.fixture_http) for url in self.urls}:
            raise OutboundDenied()
        return canonical

    async def addresses(self, host: str, port: int) -> tuple[str, ...]:
        endpoints = [
            urlsplit(canonical_url(url, allow_http=self.fixture_http)) for url in self.urls
        ]
        if not any(
            url.hostname == host and (url.port or (443 if url.scheme == "https" else 80)) == port
            for url in endpoints
        ):
            raise OutboundDenied()
        answers = await self.resolver(host, port)
        if not answers or len(answers) > 64:
            raise OutboundDenied()
        for address in answers:
            ip = ipaddress.ip_address(address)
            if str(ip) in self.fixture_ips:
                continue
            if not ip.is_global or ip.is_multicast or ip.is_reserved:
                raise OutboundDenied()
            if isinstance(ip, ipaddress.IPv6Address) and (
                ip.ipv4_mapped
                or ip.sixtofour
                or ip.teredo
                or ip in ipaddress.ip_network("64:ff9b::/96")
                or ip in ipaddress.ip_network("64:ff9b:1::/48")
            ):
                raise OutboundDenied()
        return answers


class PinnedBackend(httpcore2.AsyncNetworkBackend):
    def __init__(self, policy: OutboundPolicy):
        self.policy, self.backend = policy, httpcore2.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # Resolve once, check every answer, then dial a numeric IP. TLS keeps the original hostname.
        addresses = await self.policy.addresses(host, port)
        return await self.backend.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class _ResponseStream(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class PinnedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, policy: OutboundPolicy, *, ssl_context: ssl.SSLContext | None = None):
        self.policy = policy
        self.pool = httpcore2.AsyncConnectionPool(
            ssl_context=ssl_context or ssl.create_default_context(),
            network_backend=PinnedBackend(policy),
            retries=0,
            max_connections=8,
            max_keepalive_connections=4,
            http2=False,
        )

    async def handle_async_request(self, request):
        self.policy.validate_url(str(request.url))
        # Do not allow caller-supplied SNI/trace hooks to change the reviewed TLS identity.
        extensions = {k: v for k, v in request.extensions.items() if k == "timeout"}
        host = request.url.netloc.decode("ascii")
        if request.headers.get("host", host) != host:
            raise OutboundDenied()
        response = await self.pool.handle_async_request(
            httpcore2.Request(
                method=request.method,
                url=httpcore2.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=extensions,
            )
        )
        if 300 <= response.status < 400:
            await response.aclose()
            raise OutboundDenied()
        return httpx2.Response(
            response.status,
            headers=response.headers,
            stream=_ResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()


def outbound_client(policy: OutboundPolicy, *, ssl_context=None, hooks=None) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=PinnedTransport(policy, ssl_context=ssl_context),
        trust_env=False,
        follow_redirects=False,
        timeout=httpx2.Timeout(15),
        event_hooks=hooks or {},
    )
