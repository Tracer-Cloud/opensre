"""Pure text classifiers for interactive-shell prompt turns."""

from __future__ import annotations

_CONFIRMATION_TOKENS: frozenset[str] = frozenset({"", "y", "yes", "n", "no"})
_CANCEL_REQUEST_TOKENS: frozenset[str] = frozenset({"/cancel", "/stop", "/abort"})


def looks_like_confirmation_answer(text: str | None) -> bool:
    return (text or "").strip().lower() in _CONFIRMATION_TOKENS


def looks_like_cancel_request(text: str | None) -> bool:
    return (text or "").strip().lower() in _CANCEL_REQUEST_TOKENS


def looks_like_goal_pause_request(text: str | None) -> bool:
    """Return whether ``text`` is the exact literal ``/goal pause`` control."""
    return (text or "").strip().lower().split() == ["/goal", "pause"]


__all__ = [
    "looks_like_cancel_request",
    "looks_like_confirmation_answer",
    "looks_like_goal_pause_request",
]
