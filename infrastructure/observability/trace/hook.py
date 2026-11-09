"""Optional ``@traceable`` hook — free when session tracing is inactive.

When a real :class:`~infrastructure.observability.trace.spans.SessionTraceStore` is
registered (REPL JSONL), wraps the callable in :func:`timed_span`. When the
default noop sink is active, the wrapper is a near-zero-cost pass-through
(``isinstance`` check only — no clock, no I/O).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])


def traceable(name: str = "", **_kwargs: Any) -> Callable[[_F], _F]:
    """Wrap ``fn`` in a session-trace component span when tracing is active.

    Extra keyword arguments are accepted for forward compatibility with
    call sites that pass metadata; they are ignored.
    """
    del _kwargs

    def decorator(fn: _F) -> _F:
        span_name = name or getattr(fn, "__qualname__", getattr(fn, "__name__", "callable"))

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from infrastructure.observability.trace.spans import (
                component_span,
                is_session_trace_active,
            )

            if not is_session_trace_active():
                return fn(*args, **kwargs)
            with component_span(str(span_name)):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
