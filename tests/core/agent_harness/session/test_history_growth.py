"""``session.history`` must stop hoarding old response bodies.

Every turn appends a row and nothing ever removes one. The rows themselves are
small, but a row can carry ``response_text`` — a full agent reply — so a long
session ends up holding every answer it has ever given.

The list length has to keep growing: it is the prompt's turn counter, it is what
``/status`` reports, and ``action_driver`` captures ``len(history)`` before a
turn and slices from that index afterwards to count what the turn executed.
Removing rows would break all three. So the bound is on the *bodies*, and these
tests pin both halves: old bodies go, everything else stays.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.persistence.contracts import SessionStore
from core.agent_harness.session.session_core import RESPONSE_TEXT_WINDOW, SessionCore


class _NullStorage:
    """Storage that drops writes, so these tests touch no disk."""

    def open_session(self, session: Any) -> None:
        """Ignore session open."""

    def append_turn(self, session: Any, kind: str, text: str) -> None:
        """Ignore the turn write."""

    def flush(self, session: Any) -> None:
        """Ignore flush."""

    def __getattr__(self, _name: str) -> Any:
        return lambda *_args, **_kwargs: None


def _session() -> SessionCore:
    return SessionCore(store=cast_storage(_NullStorage()))


def cast_storage(obj: Any) -> SessionStore:
    """Narrow the stub to the port without importing ``typing.cast`` at each use."""
    return obj


def _record_turns(session: SessionCore, count: int, *, body: str = "full reply") -> None:
    for i in range(count):
        session.record("chat", f"turn {i}", response_text=f"{body} {i}")


def test_old_response_bodies_are_dropped_but_rows_remain() -> None:
    """Rows outside the window keep their identity and lose only the body."""
    # Arrange
    session = _session()
    total = RESPONSE_TEXT_WINDOW * 3

    # Act
    _record_turns(session, total)

    # Assert
    assert len(session.history) == total, "rows must never be removed"
    stale = session.history[: total - RESPONSE_TEXT_WINDOW - 1]
    assert stale, "expected some rows to have aged out"
    assert all("response_text" not in row for row in stale)
    # Identity fields survive: the reverse scans read these.
    assert all(row["type"] == "chat" and row["ok"] is True for row in stale)
    assert all(row["text"].startswith("turn ") for row in stale)


def test_recent_response_bodies_are_kept() -> None:
    """Anything a prompt or a latest-of-kind lookup can reach stays intact."""
    # Arrange
    session = _session()

    # Act
    _record_turns(session, RESPONSE_TEXT_WINDOW * 3)

    # Assert
    recent = session.history[-RESPONSE_TEXT_WINDOW:]
    assert all("response_text" in row for row in recent)


def test_turn_counter_and_captured_index_still_work() -> None:
    """The three consumers that depend on list length must be unaffected.

    ``len(history)`` is the prompt's turn number and ``/status``'s count, and
    ``action_driver`` slices from an index captured before the turn.
    """
    # Arrange
    session = _session()
    _record_turns(session, RESPONSE_TEXT_WINDOW * 2)
    history_start = len(session.history)

    # Act
    session.record("cli_agent", "the turn's own action", response_text="did the thing")
    session.record("cli_agent", "a second action")

    # Assert
    assert len(session.history) == history_start + 2
    this_turn = session.history[history_start:]
    assert [row["text"] for row in this_turn] == [
        "the turn's own action",
        "a second action",
    ]
    # The current turn's body is what action_driver reads back.
    assert this_turn[0]["response_text"] == "did the thing"


def test_memory_stops_growing_with_the_bodies() -> None:
    """A long session must not scale with the size of every past reply."""
    # Arrange
    big_body = "x" * 4096
    bounded = _session()

    # Act
    _record_turns(bounded, 500, body=big_body)
    retained = sum(len(row.get("response_text", "")) for row in bounded.history)

    # Assert
    assert retained <= RESPONSE_TEXT_WINDOW * (len(big_body) + 32), (
        f"retained {retained} bytes of response bodies across 500 turns"
    )
