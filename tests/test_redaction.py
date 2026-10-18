import logging
import sys

from backend.core.logging import RedactingLogFilter
from backend.core.redaction import REDACTED, redact_data, redact_text


def test_redacts_common_secrets_and_personal_identifiers() -> None:
    source = (
        "password=abc123 Authorization: Bearer token.value "
        "email=user@example.com db=postgresql://postgres:pw@localhost/db "
        "card=4111 1111 1111 1111"
    )

    result = redact_text(source)

    assert "abc123" not in result
    assert "token.value" not in result
    assert "user@example.com" not in result
    assert "postgres:pw" not in result
    assert "4111 1111 1111 1111" not in result


def test_redacts_nested_metadata_by_key_and_value() -> None:
    result = redact_data(
        {
            "api_key": "secret-value",
            "nested": {"message": "contact user@example.com", "safe": 3},
            "items": [{"access_token": "token"}],
        }
    )

    assert result["api_key"] == REDACTED
    assert result["nested"]["message"] == "contact [REDACTED_EMAIL]"
    assert result["nested"]["safe"] == 3
    assert result["items"][0]["access_token"] == REDACTED


def test_logging_filter_redacts_message_and_traceback() -> None:
    try:
        raise RuntimeError("password=hidden user@example.com")
    except RuntimeError:
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Bearer secret-token",
            args=(),
            exc_info=sys.exc_info(),
        )

    assert RedactingLogFilter().filter(record) is True
    assert "secret-token" not in record.getMessage()
    assert "hidden" not in (record.exc_text or "")
    assert "user@example.com" not in (record.exc_text or "")


def test_logging_filter_preserves_uvicorn_access_arguments() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:1234",
            "GET",
            "/callback?access_token=secret",
            "1.1",
            200,
        ),
        exc_info=None,
    )

    assert RedactingLogFilter().filter(record) is True
    assert len(record.args) == 5
    assert "secret" not in str(record.args)
