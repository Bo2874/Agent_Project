"""Fixed Google API origins, bounded JSON and sanitized errors; no model HTTP passthrough."""

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

    async def request(self, method, url, *, token=None, **kwargs):
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
                        data = {}
                    if response.status_code >= 300:
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
                            502, "Google request failed", "google_provider_error"
                        )
                    if not isinstance(data, dict):
                        raise GoogleServiceError(502, "Invalid Google response")
                    return data
        except httpx.HTTPError:
            raise GoogleServiceError(
                502, "Google network outcome unavailable", "google_network_error"
            ) from None
