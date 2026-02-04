"""Lightweight tool decorator for optional tool metadata."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def tool[F: Callable[..., Any]](
    func: F | None = None,
    **_kwargs: Any,
) -> F | Callable[[F], F]:
    if func is None:
        def wrapper(inner: F) -> F:
            return inner
        return wrapper
    return func
