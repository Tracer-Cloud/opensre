"""Lightweight tool decorator for optional tool metadata."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def tool(func: F | None = None, **_kwargs: Any) -> F | Callable[[F], F]:
    if func is None:
        def wrapper(inner: F) -> F:
            return inner
        return wrapper
    return func
