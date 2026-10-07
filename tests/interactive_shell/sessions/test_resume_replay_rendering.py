"""Replay fidelity for ``/resume``: the transcript, not the bookkeeping log.

Session history is append-only *bookkeeping*: one slash command writes a
``slash`` stub, a ``cli_agent`` stub, and an analytics payload as its
"response". Replaying those rows verbatim showed each command three times, the
last of them in the assistant gutter as if the model had said it.
"""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.command_registry.session_cmds.resume_rendering import (
    render_resumed_session_history,
)


def _render(history: list[dict], turn_details: list[dict], *, width: int = 78) -> str:
    buffer = io.StringIO()
    render_resumed_session_history(
        Console(file=buffer, force_terminal=False, highlight=False, width=width),
        history=history,
        turn_details=turn_details,
        messages=[],
    )
    return buffer.getvalue()


def _slash_rows(text: str) -> list[dict]:
    """The two history rows one dispatched slash command actually writes."""
    return [
        {"kind": "slash", "text": text, "timestamp": "2026-10-07T09:00:00+00:00"},
        {"kind": "cli_agent", "text": text, "timestamp": "2026-10-07T09:00:01+00:00"},
    ]


def test_slash_turn_replays_once_and_never_shows_its_analytics_payload() -> None:
    """The headline defect: ``$ /version`` + ``❯ /version`` + ``● slash … (succeeded)``.

    A failure keeps its message, because that is the part the reader can act on;
    a success with nothing to add stays silent rather than echoing the command.
    """
    output = _render(
        _slash_rows("/version") + _slash_rows("/model set"),
        [
            {"prompt": "/version", "response": "slash /version (succeeded)"},
            {
                "prompt": "/model set",
                "response": "slash /model set (failed)\nRun /logout first.",
            },
        ],
    )

    assert output.count("/version") == 1
    assert output.count("/model set") == 1
    assert "(succeeded)" not in output
    assert "(failed)" not in output
    assert "● slash" not in output
    assert "✗ Run /logout first." in output


def test_internal_choose_turn_is_not_replayed() -> None:
    """``/choose`` drives an exclusive-stdin picker; the live echo never paints it."""
    output = _render(_slash_rows("/choose") + _slash_rows("/version"), [])

    assert "/choose" not in output
    assert "▌ /version" in output


def test_a_handlers_own_outcome_prose_survives_the_replay() -> None:
    """An ``outcome_hint`` is user-facing text, not a parseable analytics payload."""
    output = _render(
        _slash_rows("/auto"),
        [{"prompt": "/auto", "response": "auto-approve: high"}],
    )

    assert "✓ auto-approve: high" in output


def test_replayed_rows_fit_every_width_and_carry_no_padding() -> None:
    """Replay goes through the same renderables as a live turn, so the full-screen
    transcript lays it out again on resize instead of keeping fixed-width bytes."""
    history = [
        {"kind": "cli_agent", "text": "why did the deploy to prod-us-east fail at 03:12?"},
    ]
    details = [
        {
            "prompt": "why did the deploy to prod-us-east fail at 03:12?",
            "response": "Four of nine pods never pulled the image tag.",
        }
    ]
    for width in (78, 44, 26):
        for row in (row for row in _render(history, details, width=width).splitlines() if row):
            assert len(row) <= width, (width, len(row), row)
            assert row == row.rstrip(), (width, repr(row))
