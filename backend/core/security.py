from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash

from backend.core.config import get_settings

ALGORITHM = "HS256"
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash("dummy-password-for-timing-protection")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def create_access_token(user_id: UUID, role: str) -> str:
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.auth_token_expire_minutes)
    payload = {"sub": str(user_id), "role": role, "exp": expires_at, "iat": datetime.now(UTC)}
    return jwt.encode(payload, settings.auth_secret_key.get_secret_value(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> UUID | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret_key.get_secret_value(),
            algorithms=[ALGORITHM],
        )
        subject = payload.get("sub")
        return UUID(subject) if isinstance(subject, str) else None
    except (InvalidTokenError, ValueError):
        return None
