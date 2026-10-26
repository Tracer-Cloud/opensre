"""Per-session record of which turns may feed memory extraction, and when a pass is due.

A turn belongs to a demo or onboarding run when, as it is recorded, the
session's active skill is the onboarding skill or one of its demo children, or
the turn wrote a plan such a skill owns (a demo's last step settles its plan
and can release the skill in the same turn). ``/demo`` and the first-run picker
enter the onboarding skill, so their turns qualify too. The signal is
structured session state, never the wording of the request.

Demo turns are remembered by their prompt turn id (matched against the
session log) and their user text (matched against the in-memory transcript)
so extraction can drop them. Only non-demo turns count toward the extraction
interval. The newest recorded turn is remembered too, so a pass can find where
the session stood when it was queued. The record lives in this process, keyed
by session id, because some hosts (the gateway) rebuild the session object for
every turn; a pass takes a snapshot of it when it is queued.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from config.constants.skills import ONBOARDING_LEAF_CHOICES, ONBOARDING_SKILL_NAME
from core.agent_harness.task_plan.evidence import plan_written_this_turn

#: Non-demo turns recorded between two background extraction passes.
EXTRACTION_TURN_INTERVAL = 6

_DEMO_SKILLS = frozenset(
    {ONBOARDING_SKILL_NAME, *(name for name, _label in ONBOARDING_LEAF_CHOICES)}
)
_MAX_TRACKED_SESSIONS = 256
_MAX_DEMO_TURNS_PER_SESSION = 200


def turn_is_demo(session: Any) -> bool:
    """True when the turn just recorded ran inside the onboarding skill tree."""
    if getattr(session, "active_skill", None) in _DEMO_SKILLS:
        return True
    plan = getattr(session, "task_plan", None)
    if getattr(plan, "owner", None) not in _DEMO_SKILLS:
        return False
    return plan_written_this_turn(session)


def normalize_user_text(text: str) -> str:
    """Whitespace-insensitive key for matching a turn's user text."""
    return " ".join(text.split())


def latest_user_text(messages: Sequence[tuple[str, str]]) -> str:
    """The newest user message of a transcript; ``""`` when there is none."""
    return next((text for role, text in reversed(messages) if role == "user"), "")


@dataclass(frozen=True)
class DemoTurns:
    """The demo turns of one session, and its newest recorded turn, as extraction needs them."""

    turn_ids: frozenset[str] = frozenset()
    user_texts: frozenset[str] = frozenset()
    #: Whether the newest recorded turn was a demo turn (its log messages may not be written yet).
    latest_is_demo: bool = False
    #: Prompt turn id and normalized user text of the newest recorded turn, when known.
    latest_turn_id: str | None = None
    latest_user_text: str = ""

    def latest_turn_id_for(self, user_text: str) -> str | None:
        """The newest turn's id when ``user_text`` is that turn's text, else ``None``."""
        if self.latest_turn_id and normalize_user_text(user_text) == self.latest_user_text:
            return self.latest_turn_id
        return None

    def covers(self, *, turn_id: str | None, user_text: str) -> bool:
        """Whether a logged turn is one of these demo turns.

        A turn with a turn id is matched by id only, so a short reply such as
        ``yes`` given both inside and after a demo is not confused.
        """
        if turn_id:
            return turn_id in self.turn_ids
        return bool(user_text) and normalize_user_text(user_text) in self.user_texts


@dataclass
class _SessionTurns:
    turns_since_pass: int = 0
    demo_turn_ids: list[str] = field(default_factory=list)
    demo_user_texts: list[str] = field(default_factory=list)
    latest_is_demo: bool = False
    latest_turn_id: str | None = None
    latest_user_text: str = ""


_lock = threading.Lock()
_sessions: OrderedDict[str, _SessionTurns] = OrderedDict()


def _entry(session_id: str) -> _SessionTurns:
    entry = _sessions.get(session_id)
    if entry is None:
        entry = _sessions[session_id] = _SessionTurns()
        while len(_sessions) > _MAX_TRACKED_SESSIONS:
            _sessions.popitem(last=False)
    else:
        _sessions.move_to_end(session_id)
    return entry


def note_recorded_turn(session_id: str, *, demo: bool, turn_id: str | None, user_text: str) -> bool:
    """Record one turn; ``True`` when it completes an extraction interval.

    Demo turns are remembered for filtering and never complete an interval.
    """
    key = normalize_user_text(user_text)
    with _lock:
        entry = _entry(session_id)
        entry.latest_is_demo = demo
        entry.latest_turn_id = turn_id
        entry.latest_user_text = key
        if demo:
            if turn_id:
                entry.demo_turn_ids.append(turn_id)
                del entry.demo_turn_ids[:-_MAX_DEMO_TURNS_PER_SESSION]
            if key:
                entry.demo_user_texts.append(key)
                del entry.demo_user_texts[:-_MAX_DEMO_TURNS_PER_SESSION]
            return False
        entry.turns_since_pass += 1
        if entry.turns_since_pass < EXTRACTION_TURN_INTERVAL:
            return False
        entry.turns_since_pass = 0
        return True


def demo_turns(session_id: str) -> DemoTurns:
    """The demo turns recorded for ``session_id`` in this process."""
    with _lock:
        entry = _sessions.get(session_id)
        if entry is None:
            return DemoTurns()
        return DemoTurns(
            turn_ids=frozenset(entry.demo_turn_ids),
            user_texts=frozenset(entry.demo_user_texts),
            latest_is_demo=entry.latest_is_demo,
            latest_turn_id=entry.latest_turn_id,
            latest_user_text=entry.latest_user_text,
        )


def forget_session(session_id: str) -> None:
    """Drop a closed session's record."""
    with _lock:
        _sessions.pop(session_id, None)


__all__ = [
    "EXTRACTION_TURN_INTERVAL",
    "DemoTurns",
    "demo_turns",
    "forget_session",
    "latest_user_text",
    "normalize_user_text",
    "note_recorded_turn",
    "turn_is_demo",
]
