"""Peek helpers for collapsed tool output in the transcript.

Pure string logic so collapse behaviour is testable without a live TTY: a long
tool result renders as a short head plus a Droid-style expand marker
(``… N more, Ctrl+O to view``). The shell binds Ctrl+O to page the full text.
"""

from __future__ import annotations

import re

DISPLAY_OUTPUT_MAX_LINES = 12
DISPLAY_OUTPUT_MAX_CHARS = 800
_BOX_DRAWING_RE = re.compile(r"[─-╿]")
_VIEW_ALL_MARKER = "Ctrl+O to view all"


def _looks_like_boxed_table(text: str) -> bool:
    """True when *text* contains box-drawing characters (a rendered table)."""
    return bool(_BOX_DRAWING_RE.search(text))


def build_output_peek(full_text: str, *, max_lines: int = 3) -> tuple[str, int]:
    """Return ``(peek, hidden_line_count)`` for *full_text*.

    The peek is the first *max_lines* lines; ``hidden`` is how many lines were
    dropped (0 when the text already fits, in which case *peek* is the whole
    text). The caller appends :func:`format_expand_marker` when ``hidden > 0``.
    """
    if not full_text.strip():
        return "", 0
    body = full_text.rstrip("\n")
    lines = body.split("\n")
    if len(lines) <= max_lines:
        return body, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def format_expand_marker(hidden: int) -> str:
    """Folded remainder: ``… N more, Ctrl+O to view``."""
    return f"… {hidden} more, Ctrl+O to view"


def format_view_all_marker() -> str:
    """Character-cap with no hidden lines: ``Ctrl+O to view all``."""
    return _VIEW_ALL_MARKER


def cap_output_for_display(
    text: str,
    *,
    max_lines: int = DISPLAY_OUTPUT_MAX_LINES,
    max_chars: int = DISPLAY_OUTPUT_MAX_CHARS,
) -> tuple[str, str | None]:
    """Return ``(preview, full_if_folded)``.

    *full_if_folded* is the original text when the preview was truncated (so
    Ctrl+O can restore it), or None when the preview is the whole text. A
    mid-line character-cap is an inline ``…``; hidden lines add one expand
    marker. Never two marker lines. A bordered table is never folded or
    character-capped — a partial box reads as corrupted.
    """
    if not text:
        return text, None
    # A bordered table shreds if folded mid-box or capped mid-line, so show
    # the complete box — every row and the closing border — and never apply
    # the character cap.
    if _looks_like_boxed_table(text):
        return text, None
    peek, hidden = build_output_peek(text, max_lines=max_lines)
    char_truncated = False
    if len(peek) > max_chars:
        peek = peek[:max_chars].rstrip() + "…"
        char_truncated = True
    if hidden:
        return f"{peek}\n{format_expand_marker(hidden)}", text
    if char_truncated:
        return f"{peek}\n{format_view_all_marker()}", text
    return peek, None


__all__ = [
    "DISPLAY_OUTPUT_MAX_CHARS",
    "DISPLAY_OUTPUT_MAX_LINES",
    "build_output_peek",
    "cap_output_for_display",
    "format_expand_marker",
    "format_view_all_marker",
]
