"""The Langfuse ``session_id`` in effect for the current turn.

The outermost turn owns the trace session: the first ``run_turn`` on a call
path binds its session id, and every ``run_turn`` nested inside it — a loop
run from a slash command, a tool that drives a headless turn — inherits that
id instead of stamping the throwaway session it was built on. A host whose
work runs on foreign threads (the scheduler's pool) binds explicitly, because
``ContextVar`` state does not cross a thread pool.

Leaf module: no dependency on the observation sink, so hosts can import it
without pulling the exporter.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TraceSession:
    """Session id plus the tags and metadata every trace inside it should carry."""

    session_id: str
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


_CURRENT: ContextVar[TraceSession | None] = ContextVar("opensre_trace_session", default=None)


def current_trace_session() -> TraceSession | None:
    """The bound trace session, or ``None`` outside any turn."""
    return _CURRENT.get()


@contextmanager
def inherit_trace_session(
    session_id: str | None,
    *,
    tags: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[TraceSession | None]:
    """Bind ``session_id`` unless an outer turn already bound one; yield the effective session.

    An outer binding keeps its session id but gains ``tags`` and ``metadata``
    (inner values win on a key clash), so a scheduled tick run from inside a
    turn stays in that turn's session and is still attributed as scheduled.
    Yields ``None`` only when nothing is bound and ``session_id`` is empty.
    """
    bound = _CURRENT.get()
    if bound is not None:
        effective = _extend(bound, tags, metadata)
    elif session_id:
        effective = TraceSession(session_id, tags, dict(metadata or {}))
    else:
        yield None
        return
    token = _CURRENT.set(effective)
    try:
        yield effective
    finally:
        _CURRENT.reset(token)


def _extend(
    outer: TraceSession, tags: tuple[str, ...], metadata: Mapping[str, Any] | None
) -> TraceSession:
    if not tags and not metadata:
        return outer
    merged_tags = outer.tags + tuple(tag for tag in tags if tag not in outer.tags)
    return TraceSession(outer.session_id, merged_tags, {**outer.metadata, **(metadata or {})})


__all__ = ["TraceSession", "current_trace_session", "inherit_trace_session"]
