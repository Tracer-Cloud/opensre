"""What one scheduled run attempt changed, and the note it left for the next run.

The executor binds one :class:`RunActivity` around an attempt, beside the
submitted-message collector. Every headless turn of the attempt records the
tool calls that changed something into it
(:func:`~infrastructure.scheduling.scheduler.tool_actions.bound_action_hook`),
and a runner keeps the note its turn's reply left for the next run. The
attempt's run record keeps both, so the loop's next tick can read them.

``ContextVar`` state does not cross a thread pool: look the activity up on the
thread that runs the attempt, and hand the object itself to other threads.
Text is credential-redacted before it is shortened.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock

from infrastructure.safety.secret_redaction import redact_text

#: An attempt keeps its newest actions; older ones are only counted.
ACTIONS_KEPT = 20
#: Longest action line kept, in characters.
ACTION_MAX_CHARS = 200
#: Longest carry-forward note kept, in characters.
CARRY_NOTE_MAX_CHARS = 300


def compact_text(text: str, max_chars: int) -> str:
    """Collapse whitespace to single spaces and cut to ``max_chars`` with an ellipsis."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max(max_chars - 1, 0)].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """A consistent copy of an attempt's activity for its run record."""

    actions: tuple[str, ...] = ()
    action_count: int = 0
    carry_note: str = ""


class RunActivity:
    """Actions one attempt's turns took and the note its reply left; safe across threads."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._actions: list[str] = []
        #: How often each distinct action happened, kept or not.
        self._times: dict[str, int] = {}
        self._note = ""

    def add_action(self, action: str) -> None:
        """Keep ``action`` once and count repeats; past ``ACTIONS_KEPT`` the oldest is dropped.

        Two calls can read the same once their payloads are left out (two
        ``git commit …``), so a repeat is counted rather than ignored.
        """
        text = compact_text(redact_text(action), ACTION_MAX_CHARS)
        if not text:
            return
        with self._lock:
            times = self._times.get(text, 0)
            self._times[text] = times + 1
            if times:
                return
            self._actions.append(text)
            del self._actions[:-ACTIONS_KEPT]

    def keep_note(self, note: str) -> None:
        """Keep the note the attempt's reply left for the next run, replacing an earlier one."""
        text = compact_text(redact_text(note), CARRY_NOTE_MAX_CHARS)
        with self._lock:
            self._note = text

    def snapshot(self) -> ActivitySnapshot:
        """The kept actions in the order they happened, every action counted, and the note.

        An action that happened more than once ends in ``(×N)``.
        """
        with self._lock:
            actions = tuple(_with_times(text, self._times[text]) for text in self._actions)
            return ActivitySnapshot(actions, len(self._times), self._note)


def _with_times(text: str, times: int) -> str:
    if times < 2:
        return text
    suffix = f" (×{times})"
    return compact_text(text, ACTION_MAX_CHARS - len(suffix)) + suffix


_CURRENT: ContextVar[RunActivity | None] = ContextVar("opensre_run_activity", default=None)


@contextmanager
def collect_run_activity() -> Iterator[RunActivity]:
    """Bind a fresh activity for the duration of the block."""
    activity = RunActivity()
    token = _CURRENT.set(activity)
    try:
        yield activity
    finally:
        _CURRENT.reset(token)


def current_run_activity() -> RunActivity | None:
    """The activity bound on this thread, if a run attempt is being recorded."""
    return _CURRENT.get()


def keep_carry_note(note: str) -> None:
    """Keep ``note`` with the attempt bound on this thread, if any."""
    activity = _CURRENT.get()
    if activity is not None:
        activity.keep_note(note)


__all__ = [
    "ACTIONS_KEPT",
    "ACTION_MAX_CHARS",
    "CARRY_NOTE_MAX_CHARS",
    "ActivitySnapshot",
    "RunActivity",
    "collect_run_activity",
    "compact_text",
    "current_run_activity",
    "keep_carry_note",
]
