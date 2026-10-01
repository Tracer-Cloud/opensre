"""Write-ahead logging for action-turn tool execution.

The ReAct loop emits ``ToolExecutionStartEvent`` synchronously, immediately
before each call executes, and ``ToolExecutionEndEvent`` after. Recording the
intent on Start (durably, fsynced) and the commit on End therefore gives the
write-ahead property with no loop changes: a ``tool_intent`` record with no
matching ``tool_call`` commit after the process died means that call was
interrupted mid-execution. A call that never started — skipped after an
earlier call ended the turn, or part of a rejected batch — gets no intent
record, only its error commit. Recovery re-discovers state instead of
replaying — see ``core.agent_harness.session.persistence.wal_recovery``.
"""

from __future__ import annotations

import itertools
import json
import logging
from typing import Any

from core.agent_harness.session import default_session_store
from core.events import (
    RuntimeEvent,
    RuntimeEventCallback,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)

logger = logging.getLogger(__name__)

_COMMIT_RESULT_MAX_CHARS = 2_000

# Marks WAL commit records so readers can tell them apart from the
# integration-gathering tool rows that share the ``tool_call`` type.
WAL_SOURCE = "wal"


def _bounded_result(result: Any) -> str:
    if isinstance(result, str):
        return result[:_COMMIT_RESULT_MAX_CHARS]
    try:
        return json.dumps(result, ensure_ascii=False, default=str)[:_COMMIT_RESULT_MAX_CHARS]
    except (TypeError, ValueError):
        return str(result)[:_COMMIT_RESULT_MAX_CHARS]


def wal_event_recorder(session: Any, *, user_text: str | None = None) -> RuntimeEventCallback:
    """Return a runtime-event callback that records tool intents and commits.

    ``session`` only needs a ``session_id``; records go to the default session
    store. Recording failures are swallowed — durability bookkeeping must
    never break a turn.
    """
    session_id = str(getattr(session, "session_id", "") or "")
    store = default_session_store()
    seq = itertools.count(1)

    def record(event: RuntimeEvent) -> None:
        if not session_id:
            return
        try:
            if isinstance(event, ToolExecutionStartEvent):
                store.append_tool_intent(
                    session_id,
                    tool=event.tool_name,
                    arguments=dict(event.args),
                    tool_call_id=event.tool_call_id,
                    seq=next(seq),
                    user_text=user_text,
                )
            elif isinstance(event, ToolExecutionEndEvent):
                store.append_tool_call(
                    session_id,
                    tool=event.tool_name,
                    arguments=dict(event.args),
                    result=_bounded_result(event.result),
                    ok=not event.is_error,
                    source=WAL_SOURCE,
                    tool_call_id=event.tool_call_id,
                    sidecar=True,
                )
        except Exception:  # noqa: BLE001 - WAL bookkeeping must never break the turn
            logger.debug(
                "[wal] recording failed for %s; ignoring",
                getattr(event, "tool_name", type(event).__name__),
                exc_info=True,
            )

    return record


def with_wal_recording(
    callback: RuntimeEventCallback | None,
    *,
    session: Any,
    user_text: str | None = None,
) -> RuntimeEventCallback:
    """Compose the WAL recorder with an optional surface event callback.

    The WAL write runs first so the intent is on disk before any observer
    side effect (spinner, streaming print) reacts to the same event.
    """
    recorder = wal_event_recorder(session, user_text=user_text)
    if callback is None:
        return recorder

    def dispatch(event: RuntimeEvent) -> None:
        recorder(event)
        callback(event)

    return dispatch


__all__ = ["WAL_SOURCE", "wal_event_recorder", "with_wal_recording"]
