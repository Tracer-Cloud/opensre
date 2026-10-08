"""Presentation for /resume: replay a resumed session through the live renderers.

Pure rendering — takes a console plus already-loaded session data and draws it
with the same row renderables a live turn uses, so a restored turn is
indistinguishable from one just typed. Holds no lookup or orchestration logic,
so the resume command module stays focused on the resume flow.

The input is an append-only *bookkeeping* log, not a transcript: one submission
can write several rows, and a slash turn's paired "response" is an analytics
payload rather than prose. This module collapses that log back into the turns a
user took before handing them to the renderers.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from rich.console import Console, RenderableType
from rich.rule import Rule
from rich.text import Text

from surfaces.interactive_shell.telemetry import parse_terminal_turn_outcome
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, INPUT_SURFACE, TEXT
from surfaces.interactive_shell.ui.transcript import (
    TranscriptRole,
    is_internal_turn,
    is_navigation_turn,
    transcript_gutter,
    user_turn_renderable,
)
from surfaces.shared.terminal.components.rendering import print_repl_renderable

_SLASH_KIND = "slash"
#: What turn accounting records for a dispatched turn, after the handler has
#: already recorded its own ``slash`` row for the same text.
_TURN_ACCOUNTING_KIND = "cli_agent"
_HISTORY_DISPLAY_CHAT_KINDS: frozenset[str] = frozenset(
    {"chat", "cli_agent", "follow_up", "alert", "incoming_alert"}
)
_SECONDS_PER = ((86_400, "d"), (3_600, "h"), (60, "m"))


@dataclass(frozen=True)
class _ReplayTurn:
    """One turn the user actually took, recovered from consecutive history rows."""

    text: str
    is_slash: bool


def _displayable_rows(history: list[dict]) -> list[tuple[str, str]]:
    """``(kind, text)`` for the history rows a transcript may show."""
    rows: list[tuple[str, str]] = []
    for record in history:
        kind = str(record.get("kind") or "")
        text = str(record.get("text") or "")
        if not text or (kind != _SLASH_KIND and kind not in _HISTORY_DISPLAY_CHAT_KINDS):
            continue
        if is_internal_turn(text) or is_navigation_turn(text):
            continue
        rows.append((kind, text))
    return rows


def _collapse_turns(history: list[dict]) -> list[_ReplayTurn]:
    """Fold the bookkeeping rows of each submission into one replayable turn.

    A dispatched slash records its own ``slash`` row and then a
    ``_TURN_ACCOUNTING_KIND`` row for the same text; a chat turn records only
    the latter. Exactly that adjacent pair collapses — two identical
    submissions in a row stay two turns.

    Whether a turn is a slash turn follows the text, not the row's kind: only
    one of the two rows may survive onto a given branch.
    """
    rows = _displayable_rows(history)
    turns: list[_ReplayTurn] = []
    index = 0
    while index < len(rows):
        kind, text = rows[index]
        paired = (
            kind == _SLASH_KIND
            and index + 1 < len(rows)
            and rows[index + 1] == (_TURN_ACCOUNTING_KIND, text)
        )
        turns.append(_ReplayTurn(text=text, is_slash=text.startswith("/")))
        index += 2 if paired else 1
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
    """Draw a replayed prompt exactly as the live echo draws it.

    Same renderable, same marker, same plate: a restored turn that looked
    different from a live one would be the very thing this module exists to
    stop, and the plate is what marks a row as something the user said.
    """
    console.print()
    print_repl_renderable(
        console,
        user_turn_renderable(
            text,
            marker_style=str(HIGHLIGHT),
            body_style=str(TEXT),
            background=f"on {INPUT_SURFACE}",
        ),
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

    A success with nothing to add stays silent: the command row above it
    already says what ran.

    Only a payload whose status can be read gets a ``✓``/``✗``. A handler's own
    ``outcome_hint`` carries no status marker, and the persisted history row's
    ``ok`` is not restored, so the result of e.g. ``opensre onboard:
    interactive wizard failed (exit 2)`` is unknowable here — it is shown as
    plain text rather than labelled with a verdict this module cannot justify.
    """
    outcome = parse_terminal_turn_outcome(response)
    if outcome is None:
        detail = response.strip()
        if detail:
            print_repl_renderable(
                console,
                transcript_gutter(
                    Text(detail, style=str(DIM)), lead=False, role=TranscriptRole.ASSISTANT
                ),
            )
        return
    if outcome.ok and not outcome.detail:
        return
    print_repl_renderable(console, _status_row(outcome.detail or "failed", ok=outcome.ok))


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
                # A status belongs to the command above it, so it stays tight —
                # the live path prints it the same way.
                _render_slash_outcome(console, response)
            elif response:
                console.print()
                render_reply_block(console, response)
        _render_seam(console)
        return

    for role, text in messages:
        if role == "user" and not is_internal_turn(text):
            _render_user_row(console, text)
        elif role == "assistant":
            console.print()
            render_reply_block(console, text)
    _render_seam(console)


__all__ = [
    "render_resume_banner",
    "render_resumed_session_history",
    "replayable_turn_count",
]
