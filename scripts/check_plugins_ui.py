"""Offline UI acceptance in installed Chrome/Edge; isolated loopback pages + mocked API replies.

Uses the actual templates/routes/assets. No host database, login secrets or provider requests.
Run separately from pytest; requires an installed Chromium browser and the locked Python env.
"""

import argparse
import asyncio
import base64
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]


def serve(port):
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.staticfiles import StaticFiles

    from backend.dependencies import get_optional_user
    from backend.routes import pages

    enabled = ContextVar("fixture_enabled", default=True)

    async def actor(request: Request):
        enabled.set(request.headers.get("x-fixture-enabled", "true") == "true")
        role = request.headers.get("x-fixture-role", "user")
        if role == "anonymous":
            return None
        return SimpleNamespace(role=role, email="fixture@example.com", full_name="UI Fixture")

    # This local test app mounts no production API and does not run the main app lifespan.
    application = FastAPI()
    application.include_router(pages.router)
    application.mount("/static", StaticFiles(directory=ROOT / "frontend/static"), name="static")
    application.dependency_overrides[get_optional_user] = actor
    pages.get_settings = lambda: SimpleNamespace(
        v2_features=SimpleNamespace(is_enabled=lambda _: enabled.get()), deepseek_model="fixture"
    )
    uvicorn.run(application, host="127.0.0.1", port=port, access_log=False, log_level="warning")


class MockAPI:
    def __init__(self):
        manifest = json.loads((ROOT / "plugins/manifests/demo.json").read_text())
        manifest.update(id="fixture.echo", name="Fixture Echo", description="A local UI fixture.")
        self.manifest = manifest
        self.tool = "demo.tool.main.calculate"
        self.skill = "demo.skill.multi_step_calculation"
        self.user_id = "00000000-0000-4000-8000-000000000086"
        self.auth = 1
        self.plugin_enabled = True
        self.connected = False
        self.connection_enabled = False
        self.empty = False
        self.fail_next = None
        self.mutations = []
        self.oauth_bodies = []
        self.refs = []

    def response(self, request):
        url, method = request["url"], request["method"]
        path = urlsplit(url).path
        envelope = {"catalog_version": "1.0", "auth_version": self.auth}
        data = json.loads(request.get("postData", "{}"))
        if method != "GET":
            # Retain only structural fields, never credential/code/state values.
            self.mutations.append({"method": method, "path": path, "keys": sorted(data)})
            if self.fail_next:
                code, self.fail_next = self.fail_next, None
                return code, {"detail": "Synthetic conflict"}
            if path == "/api/plugins/oauth/complete":
                assert data == {
                    "state": "fixture-state",
                    "code": "fixture-code",
                    "issuer": "https://issuer.example",
                }
                self.oauth_bodies.append(True)
            elif path == "/api/plugin-connections":
                assert data == {"plugin_id": "fixture.echo", "expected_auth_version": self.auth}
                self.connected = True
            elif path.endswith("/enabled"):
                if "/admin/" in path:
                    assert data["expected_catalog_version"] == "1.0"
                    self.plugin_enabled = data["enabled"]
                else:
                    assert data["expected_auth_version"] == self.auth
                    self.connection_enabled = data["enabled"]
            elif path.endswith("/credentials"):
                assert (
                    data["value"] == "fixture-token-only"
                    and data["expected_auth_version"] == self.auth
                )
                self.refs = [
                    {
                        "id": "fixture-ref",
                        "server_id": "main",
                        "slot": "token",
                        "manifest_version": "1.0.0",
                    }
                ]
                self.connection_enabled = False
            elif "/grants/" in path:
                assert data["expected_auth_version"] == self.auth
                assert set(data["capability_ids"]) == {self.tool, self.skill}
            self.auth += 1
            return 200, {**envelope, "auth_version": self.auth, "credential_references": []}
        if path == "/api/plugins":
            return 200, {
                **envelope,
                "plugins": []
                if self.empty
                else [
                    {
                        "id": "fixture.echo",
                        "namespace": "demo",
                        "name": "Fixture Echo",
                        "version": "1.0.0",
                        "description": '<img src=x onerror="window.fixtureXss=true"> Safe text',
                        "plugin_enabled": self.plugin_enabled,
                        "grant_enabled": True,
                        "grant_manifest_version": "1.0.0",
                        "capability_ids": [self.tool, self.skill],
                        "credential_requirements": [
                            {
                                "server_id": "main",
                                "slot": "token",
                                "kind": "bearer_token",
                                "transport": "streamable_http",
                            }
                        ],
                    }
                ],
            }
        if path == "/api/plugin-connections":
            return 200, {
                **envelope,
                "connections": []
                if not self.connected
                else [
                    {
                        "id": "fixture-connection",
                        "plugin_id": "fixture.echo",
                        "enabled": self.connection_enabled,
                        "manifest_version": "1.0.0",
                        "effective_enabled": self.connection_enabled,
                        "status": "configured"
                        if self.connection_enabled
                        else "connection_disabled",
                        "runtime_connected": False,
                        "credential_references": self.refs,
                    }
                ],
            }
        if path == "/api/admin/plugins":
            return 200, {
                **envelope,
                "plugins": [{"manifest": self.manifest, "enabled": self.plugin_enabled}],
            }
        if path.endswith("/grants"):
            return 200, {**envelope, "grants": []}
        raise AssertionError(f"Unexpected UI request: {method} {path}")


class CDP:
    def __init__(self, ws, origin, mock):
        self.ws, self.origin, self.mock = ws, origin, mock
        self.pending, self.sequence, self.errors, self.tasks = {}, 0, [], set()
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        async for raw in self.ws:
            message = json.loads(raw)
            if "id" in message:
                future = self.pending.pop(message["id"])
                if "error" in message:
                    future.set_exception(RuntimeError(str(message["error"])))
                else:
                    future.set_result(message.get("result", {}))
            elif message.get("method") == "Fetch.requestPaused":
                task = asyncio.create_task(self.fulfill(message["params"]))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
            elif message.get("method") == "Runtime.exceptionThrown":
                self.errors.append("Browser JavaScript exception")

    async def command(self, method, params=None):
        self.sequence += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.sequence] = future
        await self.ws.send(
            json.dumps({"id": self.sequence, "method": method, "params": params or {}})
        )
        return await asyncio.wait_for(future, 25)

    async def fulfill(self, event):
        try:
            url = event["request"]["url"]
            if url.startswith(self.origin + "/api/"):
                code, body = self.mock.response(event["request"])
                await self.command(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": event["requestId"],
                        "responseCode": code,
                        "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                        "body": base64.b64encode(json.dumps(body).encode()).decode(),
                    },
                )
            elif url.startswith(self.origin + "/") or url == "about:blank":
                await self.command("Fetch.continueRequest", {"requestId": event["requestId"]})
            else:
                # Fonts/provider/external traffic is blocked before it reaches the network.
                await self.command(
                    "Fetch.fulfillRequest", {"requestId": event["requestId"], "responseCode": 204}
                )
        except Exception as exc:
            self.errors.append(type(exc).__name__)
            with contextlib.suppress(Exception):
                await self.command(
                    "Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "Aborted"}
                )

    async def evaluate(self, expression):
        reply = await self.command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in reply:
            raise AssertionError("DOM assertion raised a JavaScript exception")
        return reply.get("result", {}).get("value")

    async def wait(self, expression):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if await self.evaluate(f"Boolean({expression})"):
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"UI condition not met: {expression}")

    async def click(self, label):
        literal = json.dumps(label)
        await self.evaluate(
            "(() => { const b=[...document.querySelectorAll('button')].find(b=>"
            f"b.textContent.trim()==={literal} && !b.disabled && b.getClientRects().length); "
            "if(!b) throw Error('Button unavailable'); b.click(); })()"
        )

    async def navigate(self, path, role="user", enabled=True):
        await self.command(
            "Network.setExtraHTTPHeaders",
            {"headers": {"X-Fixture-Role": role, "X-Fixture-Enabled": str(enabled).lower()}},
        )
        await self.command("Page.navigate", {"url": self.origin + path})
        await self.wait("document.readyState==='complete'")


async def exercise(ws_url, origin, output):
    mock = MockAPI()
    async with websockets.connect(ws_url, max_size=16 * 1024 * 1024) as ws:
        client = CDP(ws, origin, mock)
        try:
            for domain in ("Page", "Runtime", "Network"):
                await client.command(domain + ".enable")
            await client.command("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
            await client.command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False},
            )
            await client.navigate("/plugins")
            await client.wait(
                "document.querySelector('[data-plugin-controls]')?.disabled===false && "
                "document.querySelector('.plugin-card')"
            )
            assert not await client.evaluate(
                "document.querySelector('[data-plugin-register]')!==null"
            )
            assert await client.evaluate(
                "document.querySelector('.plugin-description').textContent.includes('<img') && "
                "!document.querySelector('.plugin-description img') && !window.fixtureXss"
            )
            await client.click("Tạo kết nối")
            await client.click("Xác nhận")
            await client.wait(
                "document.querySelector('[data-plugin-status]').textContent"
                ".includes('Đã tạo kết nối')"
            )
            await client.click("Bật kết nối")
            await client.click("Xác nhận")
            await client.wait(
                "document.querySelector('.plugin-state').textContent.includes('Đã cấu hình')"
            )
            shot = await client.command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}
            )
            (output / "plugins-desktop.png").write_bytes(base64.b64decode(shot["data"]))
            await client.click("Thông tin xác thực")
            await client.evaluate(
                "document.querySelector('dialog input[type=password]').value='fixture-token-only'"
            )
            await client.click("Lưu credential")
            await client.wait(
                "document.querySelector('[data-plugin-status]').textContent"
                ".includes('Đã lưu credential')"
            )
            assert not await client.evaluate(
                "document.body.textContent.includes('fixture-token-only') || "
                "[...document.querySelectorAll('input')].some(i=>i.value==='fixture-token-only')"
            )
            mock.fail_next = 409
            before = len(mock.mutations)
            await client.click("Bật kết nối")
            await client.click("Xác nhận")
            await client.wait(
                "document.querySelector('[data-plugin-dialog-status]').textContent"
                ".includes('Dữ liệu hoặc quyền')"
            )
            assert len(mock.mutations) == before + 1
            await client.click("Hủy")
            await client.click("Thử tải lại")
            await client.wait(
                "document.querySelector('[data-plugin-controls]').disabled===false && "
                "document.querySelector('[data-plugin-retry]').hidden"
            )
            await client.evaluate(
                "document.querySelector('[data-plugin-search]').value='no-match'; "
                "document.querySelector('[data-plugin-search]').dispatchEvent(new Event('input'))"
            )
            assert await client.evaluate(
                "document.querySelector('[data-plugin-list]').textContent"
                ".includes('Không tìm thấy plugin')"
            )
            await client.navigate("/admin/plugins", role="admin")
            await client.wait(
                "document.querySelector('[data-plugin-controls]')?.disabled===false && "
                "document.querySelector('[data-plugin-admin-list] .plugin-card')"
            )
            await client.click("Cấp quyền")
            await client.wait("document.querySelector('dialog[open] input[type=checkbox]')!==null")
            await client.evaluate(
                "document.querySelector('dialog input[type=text]').value="
                f"{json.dumps(mock.user_id)}; "
                f"document.querySelector('dialog input[value={json.dumps(mock.skill)}]').click()"
            )
            await client.click("Lưu quyền")
            await client.wait(
                "document.querySelector('[data-plugin-status]').textContent"
                ".includes('Đã cập nhật quyền')"
            )
            await client.click("Tắt plugin")
            await client.click("Xác nhận")
            await client.wait(
                "document.querySelector('[data-plugin-status]').textContent"
                ".includes('Đã cập nhật trạng thái')"
            )
            assert mock.plugin_enabled is False
            shot = await client.command("Page.captureScreenshot", {"format": "png"})
            (output / "plugins-admin.png").write_bytes(base64.b64decode(shot["data"]))
            await client.click("Đăng ký manifest")
            await client.evaluate("document.querySelector('dialog textarea').value='not json'")
            before = len(mock.mutations)
            await client.click("Lưu manifest")
            await client.wait(
                "document.querySelector('[data-plugin-dialog-status]').textContent"
                ".includes('JSON hợp lệ')"
            )
            assert len(mock.mutations) == before
            await client.click("Hủy")
            await client.command(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True},
            )
            await client.navigate("/plugins")
            await client.wait(
                "document.querySelector('[data-plugin-controls]')?.disabled===false && "
                "document.querySelector('.plugin-card')"
            )
            assert await client.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
            shot = await client.command(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}
            )
            (output / "plugins-mobile.png").write_bytes(base64.b64decode(shot["data"]))
            await client.navigate("/dashboard", role="admin")
            await client.wait("document.querySelector('.welcome-card')")
            assert await client.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
            await client.navigate("/plugins")
            await client.wait("document.querySelector('[data-plugin-controls]')?.disabled===false")
            mock.empty = True
            await client.click("Tải lại")
            await client.wait(
                "document.querySelector('[data-plugin-list]').textContent"
                ".includes('Chưa có plugin được cấp quyền')"
            )
            mock.empty = False
            await client.navigate(
                "/plugins/oauth/callback?state=fixture-state&code=fixture-code&iss=https%3A%2F%2Fissuer.example"
            )
            await client.wait(
                "document.querySelector('[data-plugin-status]').textContent"
                ".includes('Đã hoàn tất OAuth')"
            )
            assert await client.evaluate(
                "window.location.pathname==='/plugins' && window.location.search===''"
            )
            assert mock.oauth_bodies == [True]
            await client.navigate("/plugins", enabled=False)
            await client.wait("document.body.textContent.includes('Plugins chưa được bật')")
            assert not await client.evaluate(
                "document.querySelector('[data-plugin-controls]')!==null"
            )
            assert not client.errors, client.errors
            return {
                "status": "passed",
                "browser": "installed Chromium headless",
                "api": "mocked; production pages/assets",
                "checks": [
                    "user/admin controls",
                    "create/enable connection",
                    "credential clear",
                    "409 no replay",
                    "search/empty",
                    "grant dependencies",
                    "invalid manifest",
                    "XSS text",
                    "mobile overflow",
                    "OAuth callback body and URL cleanup",
                    "feature off",
                ],
                "external_requests": 0,
                "host_db_mutations": 0,
                "mutation_shapes": mock.mutations,
            }
        finally:
            client.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client.reader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-fixture", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/ci/plugins-ui")
    args = parser.parse_args()
    if args.serve_fixture:
        serve(args.port)
        return
    browser = args.browser or next(
        (
            Path(p)
            for p in [
                "C:/Program Files/Google/Chrome/Application/chrome.exe",
                "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                shutil.which("chromium") or "/usr/bin/chromium",
            ]
            if Path(p).is_file()
        ),
        None,
    )
    if not browser:
        raise SystemExit("Provide --browser pointing to an installed Chromium executable")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, PYTHONUTF8="1")
    for line in (ROOT / "infra/ci/offline.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (
        tempfile.TemporaryDirectory(prefix="plugins-ui-") as directory,
        (output / "fixture.log").open("w", encoding="utf8") as log,
    ):
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "scripts.check_plugins_ui",
                "--serve-fixture",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=flags,
        )
        chrome = None
        try:
            origin = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 25
            while True:
                try:
                    if httpx.get(origin + "/login", timeout=1, trust_env=False).status_code == 303:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("Loopback fixture did not start")
                time.sleep(0.1)
            chrome = subprocess.Popen(
                [
                    str(browser),
                    "--headless=new",
                    "--remote-debugging-port=0",
                    f"--user-data-dir={directory}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-extensions",
                    "about:blank",
                ],
                stdout=log,
                stderr=log,
                creationflags=flags,
            )
            active = Path(directory) / "DevToolsActivePort"
            deadline = time.monotonic() + 25
            while not active.exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("Headless browser did not start")
                time.sleep(0.1)
            browser_port = active.read_text().splitlines()[0]
            targets = httpx.get(
                f"http://127.0.0.1:{browser_port}/json/list", trust_env=False
            ).json()
            ws_url = next(row["webSocketDebuggerUrl"] for row in targets if row["type"] == "page")
            result = asyncio.run(exercise(ws_url, origin, output))
            (output / "summary.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf8"
            )
            print(json.dumps(result))
        finally:
            for process in (chrome, server):
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    main()
