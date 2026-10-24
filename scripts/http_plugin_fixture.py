"""Local TLS MCP/OAuth fixture. No provider account, external network or production secret."""

import asyncio
import base64
import hashlib
import json
import socket
import ssl
import tempfile
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from agent_core.mcp.outbound import OutboundPolicy


class Fixture:
    def __init__(self, port, *, legacy=False, json_response=True, require_auth=False):
        self.base = f"https://fixture.test:{port}"
        self.legacy, self.require_auth = legacy, require_auth
        self.access, self.refresh, self.codes = set(), set(), {}
        self.stats = {"refreshes": 0, "exchanges": 0, "revocations": 0, "requests": 0}
        self.sni = []
        server = MCPServer("http-fixture", version="0.1.0")

        @server.tool()
        async def echo(value: str) -> dict[str, str]:
            return {"value": value}

        self.app = server.streamable_http_app(
            json_response=json_response,
            stateless_http=False,
            host="fixture.test",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["fixture.test:*"],
                allowed_origins=[self.base],
            ),
        )

    def policy(self, *paths):
        async def resolver(host, port):
            assert host == "fixture.test"
            return ("127.0.0.1",)

        return OutboundPolicy(
            tuple(self.base + path for path in paths),
            fixture_ips=frozenset({"127.0.0.1"}),
            resolver=resolver,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        path = scope["path"]
        self.stats["requests"] += 1
        if path == "/authorize":
            params = request.query_params
            code = uuid4().hex
            self.codes[code] = dict(params)
            location = (
                params["redirect_uri"]
                + "?"
                + urlencode(
                    {
                        "code": code,
                        "state": params["state"],
                        "iss": self.base,
                    }
                )
            )
            return await RedirectResponse(location, status_code=302)(scope, receive, send)
        if path in {"/token", "/revoke"}:
            values = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
            if path == "/revoke":
                self.access.discard(values["token"])
                self.refresh.discard(values["token"])
                self.stats["revocations"] += 1
                return await JSONResponse({})(scope, receive, send)
            if values.get("grant_type") == "authorization_code":
                consent = self.codes.pop(values.get("code"), None)
                challenge = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(values.get("code_verifier", "").encode()).digest()
                    )
                    .decode()
                    .rstrip("=")
                )
                valid = (
                    consent
                    and challenge == consent["code_challenge"]
                    and all(
                        values.get(key) == consent[key]
                        for key in ("client_id", "resource", "redirect_uri")
                    )
                )
                self.stats["exchanges"] += 1
            else:
                valid = (
                    values.get("refresh_token") in self.refresh
                    and values.get("resource") == self.base + "/mcp"
                )
                self.refresh.discard(values.get("refresh_token"))
                self.stats["refreshes"] += 1
            if not valid:
                return await JSONResponse({"error": "invalid_grant"}, status_code=400)(
                    scope, receive, send
                )
            access, refresh = f"fixture-access-{uuid4().hex}", f"fixture-refresh-{uuid4().hex}"
            self.access.add(access)
            self.refresh.add(refresh)
            return await JSONResponse(
                {
                    "access_token": access,
                    "refresh_token": refresh,
                    "token_type": "Bearer",
                    "expires_in": 120,
                    "resource": self.base + "/mcp",
                    "scope": "tools:read",
                }
            )(scope, receive, send)
        if path == "/redirect":
            return await RedirectResponse(
                "http://169.254.169.254/latest/meta-data", status_code=307
            )(scope, receive, send)
        if (
            self.require_auth
            and request.headers.get("authorization", "").removeprefix("Bearer ") not in self.access
        ):
            return await JSONResponse({"error": "unauthorized"}, status_code=401)(
                scope, receive, send
            )
        if request.method == "POST":
            raw = await request.body()
            message = json.loads(raw)
            if self.legacy and message.get("method") == "server/discover":
                return await JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "Legacy fixture"},
                    }
                )(scope, receive, send)
            first = True

            async def replay():
                nonlocal first
                if first:
                    first = False
                    return {"type": "http.request", "body": raw, "more_body": False}
                return await receive()

            return await self.app(scope, replay, send)
        return await self.app(scope, receive, send)


@asynccontextmanager
async def http_fixture(**kwargs):
    with tempfile.TemporaryDirectory(prefix="agent-http-fixture-") as directory:
        root = Path(directory)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture.test")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
            .not_valid_after(datetime.now(UTC) + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("fixture.test")]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        certfile, keyfile = root / "cert.pem", root / "key.pem"
        certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        keyfile.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        fixture = Fixture(listener.getsockname()[1], **kwargs)
        config = uvicorn.Config(
            fixture,
            ssl_certfile=str(certfile),
            ssl_keyfile=str(keyfile),
            log_level="critical",
            access_log=False,
            lifespan="on",
        )
        config.load()
        config.ssl.set_servername_callback(
            lambda sock, hostname, context: fixture.sni.append(hostname)
        )
        fixture.ssl_context = ssl.create_default_context(cafile=str(certfile))
        server = uvicorn.Server(config)
        server.capture_signals = _no_signals
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("TLS fixture failed to start")
                    await asyncio.sleep(0.01)
            yield fixture
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)
            listener.close()


def _no_signals():
    return nullcontext()
