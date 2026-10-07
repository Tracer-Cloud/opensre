"""Presentation for /resume: replay a resumed session through the live renderers.

Pure rendering — takes a console plus already-loaded session data and draws it
with the same row renderables a live turn uses, so a restored turn is
indistinguishable from one just typed. Holds no lookup or orchestration logic,
so the resume command module stays focused on the resume flow.

Session history is an append-only *bookkeeping* log: one slash command writes
both a ``slash`` stub and a ``cli_agent`` stub, and its paired "response" is the
analytics payload, not prose. Replaying those rows verbatim showed each command
three times. This module collapses the log back into the turns a user took.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from rich.console import Console, RenderableType
from rich.rule import Rule
from rich.text import Text

from infrastructure.terminal.theme import reply_marker_style
from surfaces.interactive_shell.telemetry import parse_terminal_turn_outcome
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, TEXT
from surfaces.interactive_shell.ui.transcript import (
    TranscriptRole,
    is_internal_turn,
    is_navigation_turn,
    transcript_gutter,
    user_turn_renderable,
)
from surfaces.shared.terminal.components.rendering import print_repl_renderable

_SLASH_KIND = "slash"
_HISTORY_DISPLAY_CHAT_KINDS: frozenset[str] = frozenset(
    {"chat", "cli_agent", "follow_up", "alert", "incoming_alert"}
)
_SECONDS_PER = ((86_400, "d"), (3_600, "h"), (60, "m"))


@dataclass(frozen=True)
class _ReplayTurn:
    """One turn the user actually took, recovered from consecutive history rows."""

    text: str
    is_slash: bool


def _collapse_turns(history: list[dict]) -> list[_ReplayTurn]:
    """Fold the bookkeeping rows for one turn into a single replayable turn.

    A slash command appends a ``slash`` stub and then a ``cli_agent`` stub with
    the same text; a chat turn appends only the latter. Consecutive rows that
    repeat a text are therefore one turn, remembered as a slash turn if any of
    them was one.
    """
    turns: list[_ReplayTurn] = []
    for record in history:
        kind = str(record.get("kind") or "")
        text = str(record.get("text") or "")
        if not text or (kind != _SLASH_KIND and kind not in _HISTORY_DISPLAY_CHAT_KINDS):
            continue
        if is_internal_turn(text) or is_navigation_turn(text):
            continue
        # Decide from the text, not the row: a dispatched slash writes a ``slash``
        # stub and a ``cli_agent`` stub, and only one of them may reach a given
        # branch. Reading the kind alone replayed the surviving row as prose and
        # painted its analytics payload in the assistant gutter.
        is_slash = text.startswith("/")
        if turns and turns[-1].text == text:
            continue
        turns.append(_ReplayTurn(text=text, is_slash=is_slash))
    return turns


def replayable_turn_count(history: list[dict]) -> int:
    """How many turns the replay will actually draw.

    The banner must count what the reader is about to see, not raw history
    rows: bookkeeping duplicates and filtered navigation would otherwise
    promise turns that never appear.
    """
    return len(_collapse_turns(history))


def _assistant_replies_by_prompt(messages: list[tuple[str, str]]) -> dict[str, deque[str]]:
    """Index each user message's assistant replies, in order, for fallback lookup."""
    replies: dict[str, deque[str]] = {}
    pending_user: str | None = None
    for role, text in messages:
        if role == "user":
            pending_user = text
        elif role == "assistant" and pending_user is not None:
            replies.setdefault(pending_user, deque()).append(text)
            pending_user = None
    return replies


def _render_user_row(console: Console, text: str) -> None:
    """Draw a replayed prompt with the same renderable the live echo uses."""
    console.print()
    print_repl_renderable(
        console,
        user_turn_renderable(text, marker_style=reply_marker_style(), body_style=str(TEXT)),
    )


def _status_row(text: str, *, ok: bool) -> RenderableType:
    """A ``✓``/``✗`` row aligned under the turn it reports on."""
    marker, style = ("✓", str(HIGHLIGHT)) if ok else ("✗", str(ERROR))
    return transcript_gutter(
        Text(f"{marker} {text}", style=style),
        lead=False,
        role=TranscriptRole.ASSISTANT,
    )


def render_resume_banner(
    console: Console,
    *,
    short_id: str,
    name: str,
    turns: int,
    last_activity: str | None = None,
) -> None:
    """Open the replayed block with what was resumed and how stale it is.

    Names only what the reader can act on: which session, how much of it came
    back, and how long ago it was live. Which persistence tier it was rebuilt
    from is an implementation detail they cannot use.
    """
    parts = [" ".join(name.split())] if name else []
    parts.append(short_id)
    if turns:
        parts.append(f"{turns} turn{'s' if turns != 1 else ''}")
    gap = _format_gap(last_activity)
    if gap:
        parts.append(gap)
    console.print()
    print_repl_renderable(
        console,
        transcript_gutter(
            Text(f"↩ {' · '.join(parts)}", style=str(DIM)),
            lead=False,
            role=TranscriptRole.ASSISTANT,
        ),
    )


def _render_slash_outcome(console: Console, response: str) -> None:
    """Draw a replayed slash turn's result as a status row, never as model prose.

    The recorded "response" for a slash turn is the analytics payload
    (``slash /version (succeeded)``); painting it in the assistant gutter
    claimed the model had said it. Successes with nothing to add stay silent —
    the command row above already says what ran.
    """
    outcome = parse_terminal_turn_outcome(response)
    if outcome is None:
        # A handler's own ``outcome_hint``: already user-facing prose.
        detail, ok = response.strip(), True
    else:
        detail, ok = outcome.detail, outcome.ok
    if ok and not detail:
        return
    print_repl_renderable(console, _status_row(detail or "failed", ok=ok))


def _format_gap(timestamp: str | None) -> str:
    """Elapsed time since the session's last activity, e.g. ``3h``; empty if unknown."""
    if not timestamp:
        return ""
    try:
        last = datetime.fromisoformat(timestamp)
    except ValueError:
        return ""
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    elapsed = (datetime.now(UTC) - last).total_seconds()
    if elapsed < 60:
        return "just now"
    for seconds, suffix in _SECONDS_PER:
        if elapsed >= seconds:
            return f"{int(elapsed // seconds)}{suffix} ago"
    return ""


def _render_seam(console: Console) -> None:
    """Close the replay with one rule marking where the live session starts again."""
    console.print()
    print_repl_renderable(console, Rule(Text("now", style=str(DIM)), style=str(DIM), align="right"))


def render_resumed_session_history(
    console: Console,
    *,
    history: list[dict],
    turn_details: list[dict],
    messages: list[tuple[str, str]],
) -> None:
    """Replay prior session activity in REPL turn order, as live-looking turns."""
    from surfaces.interactive_shell.ui.streaming.renderer import render_reply_block

    if not history and not messages:
        return

    responses = {
        str(detail.get("prompt") or ""): str(detail.get("response") or "")
        for detail in reversed(turn_details)
        if detail.get("prompt")
    }
    queued = _assistant_replies_by_prompt(messages)

    def _response_for(text: str) -> str:
        recorded = responses.get(text) or ""
        if recorded:
            return recorded
        pending = queued.get(text)
        return pending.popleft() if pending else ""

    if history:
        for turn in _collapse_turns(history):
            _render_user_row(console, turn.text)
            response = _response_for(turn.text)
            if turn.is_slash:
                _render_slash_outcome(console, response)
            elif response:
                render_reply_block(console, response)
        _render_seam(console)
        return

    for role, text in messages:
        if role == "user" and not is_internal_turn(text):
            _render_user_row(console, text)
        elif role == "assistant":
            render_reply_block(console, text)
    _render_seam(console)


__all__ = [
    "render_resume_banner",
    "render_resumed_session_history",
    "replayable_turn_count",
]
