"""The storage scope in effect for the current turn.

One process serves many customers, so the scope is bound per turn rather than
per process. Path resolution and stores read it here instead of taking a
principal argument at every call site.

The scope is a ``ContextVar``. Thread pools and ``run_in_executor`` do not
propagate ContextVars, so a turn body handed to a worker thread must be wrapped
in :func:`in_current_scope` on the calling thread.

Leaf module: only depends on :mod:`config.principal`, so any layer can import it.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from config.principal import StorageScope

_CURRENT_SCOPE: ContextVar[StorageScope | None] = ContextVar("opensre_storage_scope", default=None)


@contextmanager
def bound_storage_scope(scope: StorageScope) -> Iterator[StorageScope]:
    """Bind ``scope`` for the duration of the block."""
    token = _CURRENT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CURRENT_SCOPE.reset(token)


def current_scope() -> StorageScope | None:
    """Return the bound scope, or None when nothing has been bound."""
    return _CURRENT_SCOPE.get()


def in_current_scope[T](fn: Callable[[], T]) -> Callable[[], T]:
    """Bind the caller's context to ``fn`` so a worker thread sees the same scope.

    Call this on the thread that holds the scope, *before* handing the callable
    to ``run_in_executor`` / ``ThreadPoolExecutor.submit``. The returned callable
    runs ``fn`` in a copy of the calling context taken here, so the worker sees
    the scope the caller bound and its own ContextVar writes stay on that copy.
    """
    context = contextvars.copy_context()
    return lambda: context.run(fn)


__all__ = [
    "bound_storage_scope",
    "current_scope",
    "in_current_scope",
]
