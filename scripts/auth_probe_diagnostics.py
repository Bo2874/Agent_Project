"""Safe auth diagnostics for synthetic integration probes; never returns a token/key/subject."""

import time
from http.cookies import SimpleCookie
from uuid import UUID

import jwt

from backend.database import SessionFactory
from backend.models import User


async def auth_diagnostics(client, response, settings):
    token = client.cookies.get("access_token")
    header = response.request.headers.get("cookie", "")
    parsed = SimpleCookie()
    parsed.load(header)
    result = {
        "utc_seconds": int(time.time()),
        "cookie_sent": "access_token" in parsed,
        "cookie_matches_jar": bool(
            token and "access_token" in parsed and parsed["access_token"].value == token
        ),
        "ttl_minutes": settings.auth_token_expire_minutes,
        "cookie_expiries": [
            cookie.expires for cookie in client.cookies.jar if cookie.name == "access_token"
        ],
    }
    if token:
        claims = jwt.decode(token, options={"verify_signature": False})
        result.update({key: claims.get(key) for key in ("iat", "exp")})
        try:
            jwt.decode(token, settings.auth_secret_key.get_secret_value(), algorithms=["HS256"])
            result["jwt_valid"] = True
        except jwt.InvalidTokenError as exc:
            result["jwt_error"] = type(exc).__name__
        async with SessionFactory() as session:
            user = await session.get(User, UUID(claims["sub"]))
            result.update(
                user_exists=user is not None,
                user_active=bool(user and user.is_active),
                user_role=user.role if user else None,
            )
    return result
