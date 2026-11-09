"""Request-scoped structured connection choice, shared by runtime hosts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_github_connection: ContextVar[str | None] = ContextVar("github_connection_id", default=None)


def current_github_connection_id() -> str | None:
    return _github_connection.get()


@contextmanager
def bound_github_connection(connection_id: str | None) -> Iterator[None]:
    token = _github_connection.set(connection_id)
    try:
        yield
    finally:
        _github_connection.reset(token)
