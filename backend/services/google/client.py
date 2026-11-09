"""Fixed Google API origins, bounded JSON and sanitized errors; no model HTTP passthrough."""

import asyncio

import httpx

from backend.services.google.errors import GoogleServiceError

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
DRIVE_URL = "https://www.googleapis.com/drive/v3"
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
DOCS_URL = "https://docs.googleapis.com/v1/documents"


class GoogleHTTP:
    def __init__(self, transport=None):
        self.transport = transport

    async def download_bytes(self, url, *, token, max_bytes, params=None):
        """Only selected Drive media/export endpoints; never follow a redirect."""
        import re

        parsed = httpx.URL(url)
        if (
            parsed.scheme != "https"
            or parsed.host != "www.googleapis.com"
            or parsed.port not in (None, 443)
            or parsed.userinfo
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/drive/v3/files/[A-Za-z0-9_-]+(?:/export)?", parsed.path)
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= 20971520
        ):
            raise GoogleServiceError(422, "Unsupported Google download endpoint or limit")
        try:
            async with httpx.AsyncClient(
                timeout=60, trust_env=False, follow_redirects=False, transport=self.transport
            ) as client:
                async with client.stream(
                    "GET", url, params=params, headers={"Authorization": "Bearer " + token}
                ) as response:
                    if response.status_code != 200:
                        raise GoogleServiceError(502, "Google download unavailable")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise GoogleServiceError(413, "Google download exceeds file limit")
                    if not data:
                        raise GoogleServiceError(422, "Empty Google download")
                    return bytes(data)
        except httpx.HTTPError:
            raise GoogleServiceError(502, "Google download unavailable") from None

    async def upload_bytes(self, *, token, metadata, content, source_mime, file_id=None):
        """One bounded resumable session; its bearer-like Location never leaves trusted code."""
        if not isinstance(content, bytes) or not 1 <= len(content) <= 20971520:
            raise GoogleServiceError(413, "Google upload exceeds the 20 MiB limit")
        endpoint = "https://www.googleapis.com/upload/drive/v3/files"
        if file_id is not None:
            import re

            if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", file_id):
                raise GoogleServiceError(422, "Invalid Google upload target")
            endpoint += "/" + file_id
        headers = {"Authorization": "Bearer " + token}

        async def consume(response, *, initial=False):
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 2097152:
                    raise GoogleServiceError(502, "Google upload response exceeds limit")
            if response.status_code not in {200, 201}:
                raise GoogleServiceError(502, "Google upload outcome unavailable")
            if initial:
                return response.headers.get("Location", "")
            import json

            try:
                data = json.loads(body)
            except (ValueError, UnicodeError):
                raise GoogleServiceError(502, "Invalid Google upload response") from None
            if not isinstance(data, dict):
                raise GoogleServiceError(502, "Invalid Google upload response")
            return data

        try:
            async with httpx.AsyncClient(
                timeout=60, trust_env=False, follow_redirects=False, transport=self.transport
            ) as client:
                async with client.stream(
                    "PATCH" if file_id else "POST",
                    endpoint,
                    params={"uploadType": "resumable", "fields": "id,name,mimeType"},
                    headers={
                        **headers,
                        "X-Upload-Content-Type": source_mime,
                        "X-Upload-Content-Length": str(len(content)),
                    },
                    json=metadata,
                ) as response:
                    location = await consume(response, initial=True)
                target = httpx.URL(location)
                if (
                    len(location) > 8192
                    or target.scheme != "https"
                    or target.host != "www.googleapis.com"
                    or target.port not in (None, 443)
                    or target.userinfo
                    or target.fragment
                    or target.path != httpx.URL(endpoint).path
                    or not target.params.get("upload_id")
                ):
                    raise GoogleServiceError(502, "Unsupported Google upload session endpoint")
                async with client.stream(
                    "PUT",
                    location,
                    headers={
                        **headers,
                        "Content-Type": source_mime,
                        "Content-Length": str(len(content)),
                        "Content-Range": f"bytes 0-{len(content) - 1}/{len(content)}",
                    },
                    content=content,
                ) as response:
                    return await consume(response)
        except httpx.HTTPError:
            raise GoogleServiceError(502, "Google upload network outcome unavailable") from None

    async def request(self, method, url, *, token=None, **kwargs):
        for attempt in range(3 if method == "GET" else 1):
            try:
                return await self._request_once(method, url, token=token, **kwargs)
            except GoogleServiceError as error:
                if (
                    method != "GET"
                    or attempt == 2
                    or error.code not in {"google_network_error", "google_transient_error"}
                ):
                    raise
                await asyncio.sleep(0.1 * (attempt + 1))

    async def _request_once(self, method, url, *, token=None, **kwargs):
        parsed = httpx.URL(url)
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.userinfo
            or parsed.host
            not in {
                "oauth2.googleapis.com",
                "www.googleapis.com",
                "sheets.googleapis.com",
                "docs.googleapis.com",
            }
        ):
            raise GoogleServiceError(422, "Unsupported Google endpoint")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with httpx.AsyncClient(
                timeout=25, trust_env=False, follow_redirects=False, transport=self.transport
            ) as client:
                async with client.stream(method, url, headers=headers, **kwargs) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2097152:
                            raise GoogleServiceError(
                                422, "Google response exceeds observation limit"
                            )
                    import json

                    try:
                        data = json.loads(body)
                    except (ValueError, UnicodeError):
                        if response.status_code < 300:
                            raise GoogleServiceError(502, "Invalid Google response") from None
                        data = {}
                    if not isinstance(data, dict):
                        raise GoogleServiceError(502, "Invalid Google response")
                    if response.status_code >= 300:
                        if (
                            method == "POST"
                            and url.startswith(DOCS_URL + "/")
                            and url.endswith(":batchUpdate")
                            and kwargs.get("json", {})
                            .get("writeControl", {})
                            .get("requiredRevisionId")
                            and response.status_code in {400, 409, 412}
                            and isinstance(data.get("error"), dict)
                            and (
                                data["error"].get("status") == "FAILED_PRECONDITION"
                                or "revision" in str(data["error"].get("message", "")).lower()
                            )
                        ):
                            raise GoogleServiceError(
                                409, "Google source changed; start a new request", "google_conflict"
                            )
                        if (
                            method == "POST"
                            and url.endswith(":batchUpdate")
                            and url.startswith((SHEETS_URL + "/", DOCS_URL + "/"))
                            and response.status_code == 400
                            and isinstance(data.get("error"), dict)
                            and data["error"].get("status") == "INVALID_ARGUMENT"
                        ):
                            raise GoogleServiceError(
                                422,
                                "Google rejected the native batch without applying changes; "
                                "correct the API requests and try a new call.",
                                "google_request_rejected",
                            )
                        if url == TOKEN_URL and data.get("error") == "invalid_grant":
                            raise GoogleServiceError(
                                409, "Google reconnect required", "google_reconnect_required"
                            )
                        if response.status_code in (401, 403, 404):
                            raise GoogleServiceError(
                                404,
                                "Google file or permission unavailable",
                                "google_permission_unavailable",
                            )
                        raise GoogleServiceError(
                            502,
                            "Google request failed",
                            "google_transient_error"
                            if response.status_code in {429, 500, 502, 503, 504}
                            else "google_provider_error",
                        )
                    if not isinstance(data, dict):
                        raise GoogleServiceError(502, "Invalid Google response")
                    return data
        except httpx.HTTPError:
            raise GoogleServiceError(
                502, "Google network outcome unavailable", "google_network_error"
            ) from None
