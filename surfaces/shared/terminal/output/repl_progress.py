"""REPL-safe progress signalling without importing the interactive shell runtime."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Generator

_REPL_SAFE_PROGRESS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "repl_safe_progress",
    default=False,
)


@contextlib.contextmanager
def repl_safe_progress_scope() -> Generator[None]:
    """Mark the current context (and ``asyncio.to_thread`` children) as REPL-safe.

    Turn dispatch runs in a worker thread where ``get_app_or_none()`` is
    unset even though the main thread still has an active ``prompt_async``.  Set
    this scope around ``asyncio.to_thread`` so progress renderers avoid Rich Live.
    """
    token = _REPL_SAFE_PROGRESS.set(True)
    try:
        yield
    finally:
        _REPL_SAFE_PROGRESS.reset(token)


def repl_safe_progress_requested() -> bool:
    """True when a parent scope has marked progress as REPL-safe."""
    return _REPL_SAFE_PROGRESS.get()
