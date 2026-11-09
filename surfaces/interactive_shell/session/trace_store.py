"""JSONL-backed :class:`~infrastructure.observability.trace.spans.SessionTraceStore` for the REPL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent_harness.spi.defaults import JsonlSessionStore
from infrastructure.observability.trace.spans import NoopSessionTraceStore, SessionTraceStore


@dataclass
class JsonlSessionTraceStore:
    """Write ``trace_span`` records through the session's JSONL store."""

    store: JsonlSessionStore

    def emit(
        self,
        session_id: str,
        *,
        span_kind: str,
        name: str,
        status: str = "ok",
        duration_ms: int | None = None,
        attributes: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> str:
        return self.store.append_trace_span(
            session_id,
            span_kind=span_kind,
            name=name,
            status=status,
            duration_ms=duration_ms,
            attributes=attributes,
            parent_id=parent_id,
        )


def jsonl_trace_store_for_session(session: Any) -> SessionTraceStore:
    """Return a JSONL store bound to ``session.store``, or a Noop store for
    non-JSONL (e.g. in-memory) sessions so tests don't leak trace files to disk."""
    store = getattr(session, "store", None)
    if not isinstance(store, JsonlSessionStore):
        return NoopSessionTraceStore()
    return JsonlSessionTraceStore(store=store)


__all__ = ["JsonlSessionTraceStore", "jsonl_trace_store_for_session"]
