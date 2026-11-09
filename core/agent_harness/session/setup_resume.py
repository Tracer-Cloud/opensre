"""The user turn a missing integration parked, replayed once setup makes it runnable.

When a skill cannot start (its prerequisite gate opened a setup menu) or the
agent queues ``/integrations setup <service>`` mid-skill, the shell parks the
turn's user message here. After setup, the host re-checks the prerequisite and,
when it holds, resubmits the message exactly once, so the user lands back where
they were without retyping anything.

Only a shell terminal facet carries the record (its ``setup_resume`` field):
gateway and headless sessions, and test fakes without the field, never park a
turn, and every accessor is a no-op for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers
from core.agent_harness.session.terminal_access import session_terminal


@dataclass(frozen=True, slots=True)
class SetupResume:
    """A parked user turn and the prerequisite it waits on."""

    text: str
    """The user message to resubmit, verbatim."""

    as_answer: bool
    """True when ``text`` answers a selection menu, so the replay keeps its skill context."""

    skill: str
    """The skill whose prerequisite stopped the turn."""

    service: str
    """The integration whose setup the turn waits for (``github``)."""


def _terminal_with_record(session: Any) -> Any | None:
    terminal = session_terminal(session)
    if terminal is None or not hasattr(terminal, "setup_resume"):
        return None
    return terminal


def arm_setup_resume(session: Any, text: str, *, skill: str, service: str) -> bool:
    """Park ``text`` until ``service`` is set up for ``skill``; replaces an earlier record.

    Returns False, parking nothing, when the session has no terminal record or
    ``text`` is empty or a slash command (only a request or a menu answer is
    replayed).
    """
    terminal = _terminal_with_record(session)
    stripped = text.strip()
    if terminal is None or not stripped or stripped.startswith("/"):
        return False
    terminal.setup_resume = SetupResume(
        text=text,
        as_answer=bool(parse_ask_user_answers(text)),
        skill=skill,
        service=service,
    )
    return True


def pending_setup_resume(session: Any) -> SetupResume | None:
    """The parked turn, left in place; None when nothing is parked."""
    terminal = _terminal_with_record(session)
    record = terminal.setup_resume if terminal is not None else None
    return record if isinstance(record, SetupResume) else None


def take_setup_resume(session: Any) -> SetupResume | None:
    """Remove and return the parked turn, so it is replayed at most once."""
    record = pending_setup_resume(session)
    if record is not None:
        clear_setup_resume(session)
    return record


def clear_setup_resume(session: Any) -> None:
    """Drop the parked turn, if any (a new request, a closed menu, a new session)."""
    terminal = _terminal_with_record(session)
    if terminal is not None:
        terminal.setup_resume = None


__all__ = [
    "SetupResume",
    "arm_setup_resume",
    "clear_setup_resume",
    "pending_setup_resume",
    "take_setup_resume",
]
