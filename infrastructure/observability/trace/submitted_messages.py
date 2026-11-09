"""Collect the messages headless turns submit while a caller observes them.

A scheduled run records the exact prompt its turns sent, so a run's history
never has to be rebuilt from the loop's current configuration. The scheduler
binds a collector around one run; ``AgentSession.run_headless_turn`` reports
each message it submits. Outside a bound collector, reporting does nothing.

``ContextVar`` state does not cross a thread pool, so the collector must be
bound on the thread that runs the turns, as the scheduler's worker does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from infrastructure.observability.trace.trace_session import current_trace_session

#: A run that loops over many turns keeps its first few; the rest are counted.
_MAX_MESSAGES = 5


@dataclass(frozen=True, slots=True)
class SubmittedMessage:
    """One message a turn submitted, with the trace session it ran under."""

    text: str
    trace_session_id: str = ""


@dataclass(slots=True)
class SubmittedMessages:
    """Messages submitted inside one bound collector, in submission order."""

    messages: list[SubmittedMessage] = field(default_factory=list)
    count: int = 0


_CURRENT: ContextVar[SubmittedMessages | None] = ContextVar(
    "opensre_submitted_messages", default=None
)


@contextmanager
def collect_submitted_messages() -> Iterator[SubmittedMessages]:
    """Bind a fresh collector for the duration of the block."""
    collected = SubmittedMessages()
    token = _CURRENT.set(collected)
    try:
        yield collected
    finally:
        _CURRENT.reset(token)


def note_submitted_message(message: str) -> None:
    """Record ``message`` in the bound collector, if any."""
    collected = _CURRENT.get()
    if collected is None:
        return
    collected.count += 1
    if len(collected.messages) >= _MAX_MESSAGES:
        return
    session = current_trace_session()
    collected.messages.append(
        SubmittedMessage(message, session.session_id if session is not None else "")
    )


__all__ = [
    "SubmittedMessage",
    "SubmittedMessages",
    "collect_submitted_messages",
    "note_submitted_message",
]
