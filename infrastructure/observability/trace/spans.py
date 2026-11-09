"""Session trace span port — product instrumentation for JSONL / ATM.

Design for production safety
----------------------------
* Default store is :class:`NoopSessionTraceStore` — emit paths return immediately
  after an ``isinstance`` check (no timing, no sampling, no I/O).
* Expensive work (RSS / thread enumeration, JSONL append) runs **only** when a
  real store is registered (REPL with JSONL storage).
* Call sites should prefer the semantic helpers below (``component_span``,
  ``tool_span``, ``stage_span``, …) so business code stays readable. Prefer
  those over raw ``emit_span`` / ``timed_span`` with ``span_kind=`` kwargs.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from infrastructure.observability.trace.process_stats import sample_turn_boundary_stats

_session_id: ContextVar[str | None] = ContextVar("session_trace_session_id", default=None)

#: Convert ``time.monotonic()`` seconds to integer milliseconds for span duration.
_MS_PER_SECOND = 1000

#: Reserved attrs key: callers set this to override the span status on exit.
SPAN_STATUS_ATTR = "_status"
SPAN_STATUS_OK = "ok"
SPAN_STATUS_ERROR = "error"


class SessionTraceStore(Protocol):
    """Append-only session trace spans (routes, stages, threads, resources)."""

    def emit(
        self,
        session_id: str,
        *,
        span_kind: str,
        name: str,
        status: str = SPAN_STATUS_OK,
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        """Persist one span; return entry id (empty when persistence is unavailable)."""


class NoopSessionTraceStore:
    """Default store before a surface registers a JSONL adapter."""

    def emit(
        self,
        session_id: str,
        *,
        span_kind: str,
        name: str,
        status: str = SPAN_STATUS_OK,
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        del session_id, span_kind, name, status, duration_ms, attributes, parent_id
        return ""


_store: SessionTraceStore = NoopSessionTraceStore()


def get_session_trace_store() -> SessionTraceStore:
    return _store


def set_session_trace_store(store: SessionTraceStore | None) -> None:
    global _store
    _store = store if store is not None else NoopSessionTraceStore()


def is_session_trace_active() -> bool:
    """True when a non-noop store is registered (JSONL / ATM path)."""
    return not isinstance(_store, NoopSessionTraceStore)


def current_trace_session_id() -> str | None:
    return _session_id.get()


@contextmanager
def bind_session_trace(session_id: str | None) -> Iterator[None]:
    """Bind ``session_id`` for nested :func:`emit_span` / :func:`timed_span` calls."""
    if not session_id:
        yield
        return
    token = _session_id.set(session_id)
    try:
        yield
    finally:
        _session_id.reset(token)


def emit_span(
    *,
    span_kind: str,
    name: str,
    status: str = SPAN_STATUS_OK,
    duration_ms: int | None = None,
    attributes: dict[str, Any] | None = None,
    parent_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Emit one span when tracing is active; no-op (and free) otherwise."""
    if not is_session_trace_active():
        return ""
    sid = session_id or _session_id.get()
    if not sid:
        return ""
    return _store.emit(
        sid,
        span_kind=span_kind,
        name=name,
        status=status,
        duration_ms=duration_ms,
        attributes=attributes,
        parent_id=parent_id,
    )


@contextmanager
def timed_span(
    *,
    span_kind: str,
    name: str,
    attributes: dict[str, Any] | None = None,
    parent_id: str | None = None,
    session_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Time a region and emit a span on exit when tracing is active.

    Yields a mutable ``attrs`` dict callers may enrich before the block ends.
    When the store is noop, this is a near-zero-cost nullcontext (no clock).
    """
    attrs: dict[str, Any] = dict(attributes or {})
    if not is_session_trace_active():
        yield attrs
        return
    sid = session_id or _session_id.get()
    if not sid:
        yield attrs
        return
    started = time.monotonic()
    status = SPAN_STATUS_OK
    body_raised = False
    try:
        yield attrs
    except BaseException:
        status = SPAN_STATUS_ERROR
        body_raised = True
        raise
    finally:
        duration_ms = int((time.monotonic() - started) * _MS_PER_SECOND)
        override = attrs.pop(SPAN_STATUS_ATTR, None)
        # Only honor a caller override when the body did not raise.
        if not body_raised and isinstance(override, str) and override:
            status = override
        _store.emit(
            sid,
            span_kind=span_kind,
            name=name,
            status=status,
            duration_ms=duration_ms,
            attributes=attrs or None,
            parent_id=parent_id,
        )


def emit_thread_boundary(
    session_id: str,
    *,
    name: str,
    phase: str,
    asyncio_tasks: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Emit a ``span_kind=thread`` snapshot at a REPL turn or session boundary.

    Skips process sampling entirely when the store is noop so headless/tests
    pay only an ``isinstance`` check.
    """
    if not is_session_trace_active():
        return ""
    attributes = sample_turn_boundary_stats(asyncio_tasks=asyncio_tasks)
    attributes["phase"] = phase
    if extra:
        attributes.update(extra)
    return _store.emit(
        session_id,
        span_kind="thread",
        name=name,
        attributes=attributes,
    )


# ---------------------------------------------------------------------------
# Semantic helpers — keep call sites readable (prefer these over timed_span)
# ---------------------------------------------------------------------------


def component_span(
    name: str,
    *,
    session_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> AbstractContextManager[dict[str, Any]]:
    """Time a harness / surface component (action turn, gateway turn, …)."""
    return timed_span(
        span_kind="component",
        name=name,
        session_id=session_id,
        attributes=attributes,
    )


def stage_span(name: str) -> AbstractContextManager[dict[str, Any]]:
    """Time one pipeline stage."""
    return timed_span(span_kind="stage", name=name)


def tool_span(
    name: str,
    *,
    tool_call_id: str,
    attributes: dict[str, Any] | None = None,
) -> AbstractContextManager[dict[str, Any]]:
    """Time one tool execution (universal choke point in ``core.tool.execution``)."""
    attrs = {"tool_call_id": tool_call_id, **(attributes or {})}
    return timed_span(span_kind="tool", name=name, attributes=attrs)


def llm_span(
    name: str,
    *,
    iteration: int,
    attributes: dict[str, Any] | None = None,
) -> AbstractContextManager[dict[str, Any]]:
    """Time one LLM invoke inside the ReAct loop."""
    attrs = {"iteration": iteration, **(attributes or {})}
    return timed_span(span_kind="llm", name=name, attributes=attrs)


def loop_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> AbstractContextManager[dict[str, Any]]:
    """Time one bounded loop such as a ReAct run."""
    return timed_span(span_kind="loop", name=name, attributes=attributes)


def loop_iteration_span(
    name: str,
    *,
    iteration: int,
    attributes: dict[str, Any] | None = None,
) -> AbstractContextManager[dict[str, Any]]:
    """Time one iteration inside a bounded loop."""
    attrs = {"iteration": iteration, **(attributes or {})}
    return timed_span(span_kind="loop_iteration", name=name, attributes=attrs)


def emit_route(
    name: str,
    *,
    session_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Record the harness route decision (instant span, no duration)."""
    return emit_span(
        span_kind="route",
        name=name,
        session_id=session_id,
        attributes=attributes,
    )


@contextmanager
def traced_session(
    session_id: str | None,
    *,
    component: str,
    attributes: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Bind ``session_id`` and time a top-level component in one ``with`` block."""
    with (
        bind_session_trace(session_id),
        component_span(component, session_id=session_id, attributes=attributes) as attrs,
    ):
        yield attrs


def mark_span_outcome(
    attrs: dict[str, Any],
    outcome: str,
    *,
    error: bool = False,
    **extra: Any,
) -> None:
    """Enrich a timed-span attrs dict; set ``SPAN_STATUS_ATTR=error`` when ``error``."""
    attrs["outcome"] = outcome
    if error:
        attrs[SPAN_STATUS_ATTR] = SPAN_STATUS_ERROR
    for key, value in extra.items():
        if value is not None:
            attrs[key] = value


__all__ = [
    "NoopSessionTraceStore",
    "SPAN_STATUS_ATTR",
    "SPAN_STATUS_ERROR",
    "SPAN_STATUS_OK",
    "SessionTraceStore",
    "bind_session_trace",
    "component_span",
    "current_trace_session_id",
    "emit_route",
    "emit_span",
    "emit_thread_boundary",
    "get_session_trace_store",
    "is_session_trace_active",
    "llm_span",
    "loop_iteration_span",
    "loop_span",
    "mark_span_outcome",
    "set_session_trace_store",
    "stage_span",
    "timed_span",
    "tool_span",
    "traced_session",
]
