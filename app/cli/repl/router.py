"""Classify REPL input as slash command, new alert, or follow-up question."""

from __future__ import annotations

from typing import Literal

from app.cli.repl.session import ReplSession

InputKind = Literal["slash", "new_alert", "follow_up"]


# Bare words that map to slash commands — users often forget the leading slash.
_BARE_COMMAND_ALIASES = frozenset(
    {
        "help",
        "?",  # iconic shortcut for help, matches vim, less, many REPLs
        "exit",
        "quit",
        "clear",
        "reset",
        "status",
        "trust",
    }
)


# Short, question-shaped strings that obviously target the previous investigation.
_FOLLOW_UP_CUES = (
    "why",
    "how",
    "what",
    "was it",
    "is it",
    "explain",
    "tell me more",
    "more detail",
    "expand",
    "clarify",
)


# Cues that strongly suggest a fresh incident rather than a follow-up.
_ALERT_CUES = (
    "alert",
    "error",
    "failure",
    "failing",
    "down",
    "outage",
    "spiked",
    "spike",
    "dropped",
    "latency",
    "timeout",
    "5xx",
    "500",
    "503",
    "crash",
    "crashed",
    "cpu",
    "memory",
    "disk",
    "connection",
    "investigate",
)


def _is_short_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    # A reasonable heuristic: short (< ~90 chars) and either ends with '?' or
    # starts with a question cue.  The length guard applies to both branches so
    # that a long incident description phrased as a question ("CPU usage on
    # orders-api has been climbing steadily for the past two hours — what
    # changed?") routes to a fresh investigation instead of the follow-up path.
    if len(stripped) >= 90:
        return False
    lower = stripped.lower()
    if stripped.endswith("?"):
        return True
    return any(lower.startswith(cue) for cue in _FOLLOW_UP_CUES)


def _mentions_alert_signal(text: str) -> bool:
    lower = text.lower()
    return any(cue in lower for cue in _ALERT_CUES)


def classify_input(text: str, session: ReplSession) -> InputKind:
    """Classify a single line of REPL input.

    Rules (in order):
      1. Anything starting with ``/`` is a slash command.
      2. If there is no previous investigation, treat as a new alert.
      3. If the input is a short question, treat as a follow-up — even if it
         mentions metric names like "CPU" or "memory".  The shape of the
         sentence ("why did CPU spike?") is a stronger signal than the
         presence of a single alert-ish word.
      4. If the input has alert-shaped signals, treat as a new alert.
      5. Otherwise default to a new alert (safer — produces a fresh run rather
         than a free-floating chat message).
    """
    stripped = text.strip()
    if stripped.startswith("/"):
        return "slash"

    # A bare word that matches a known slash command is almost always a typo
    # for the slash command itself — route it there instead of triggering a
    # full investigation.
    if stripped.lower() in _BARE_COMMAND_ALIASES:
        return "slash"

    if session.last_state is None:
        return "new_alert"

    if _is_short_question(stripped):
        return "follow_up"

    if _mentions_alert_signal(stripped):
        return "new_alert"

    return "new_alert"
