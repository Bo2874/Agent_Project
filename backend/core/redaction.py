from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|passphrase|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|authorization|cookie|session|otp|cvv)",
    re.IGNORECASE,
)
_KEY_VALUE = re.compile(
    r"(?i)(password|passwd|passphrase|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|authorization|cookie|otp|cvv)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_DATABASE_URL = re.compile(r"(?i)\b(postgresql|postgres|redis)://[^\s]+")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")


def redact_text(value: object, *, max_length: int | None = None) -> str:
    text = str(value)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _KEY_VALUE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    text = _DATABASE_URL.sub(lambda match: f"{match.group(1)}://{REDACTED}", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _CARD.sub("[REDACTED_NUMBER]", text)
    return text[:max_length] if max_length is not None else text


def redact_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _SENSITIVE_KEY.search(str(key)) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
