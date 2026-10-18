from __future__ import annotations

import logging
import traceback

from backend.core.redaction import redact_text


class RedactingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            record.args = tuple(
                redact_text(value) if isinstance(value, str) else value for value in record.args
            )
        else:
            record.msg = redact_text(record.getMessage())
            record.args = ()
        if record.exc_info:
            formatted = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact_text(formatted)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        return True


def install_log_redaction() -> None:
    filter_instance = RedactingLogFilter()
    logger_names = (None, "uvicorn", "uvicorn.error", "uvicorn.access")
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        if not any(isinstance(item, RedactingLogFilter) for item in logger.filters):
            logger.addFilter(filter_instance)
        for handler in logger.handlers:
            if not any(isinstance(item, RedactingLogFilter) for item in handler.filters):
                handler.addFilter(filter_instance)
