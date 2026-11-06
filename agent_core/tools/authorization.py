"""Deduplicate shared authority only inside one explicit validation boundary."""

from contextlib import contextmanager
from contextvars import ContextVar

_checked = ContextVar("authorization_boundary_checks", default=None)


@contextmanager
def authorization_boundary():
    token = _checked.set(set())
    try:
        yield
    finally:
        _checked.reset(token)


async def check_authorization_once(key, check):
    checked = _checked.get()
    if checked is not None and key in checked:
        return
    await check()
    if checked is not None:
        checked.add(key)
