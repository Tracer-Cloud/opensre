"""Prompt-region line width and clipping (leaf — no shell runtime imports).

Live prompt rows (spinner, idle hint, auto status, overlays) must stay one
terminal line. A glyph in the last column puts the cursor in pending-wrap;
on shrink-resize the emulator soft-wraps and prompt-toolkit row accounting
drifts, leaving stale frames in scrollback.
"""

from __future__ import annotations

from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.utils import get_cwidth

from infrastructure.safety.terminal_output import strip_terminal_controls

DEFAULT_TERMINAL_COLUMNS = 80


def terminal_columns() -> int:
    """Current terminal width, or 80 when no prompt_toolkit app is active."""
    app = get_app_or_none()
    if app is None:
        return DEFAULT_TERMINAL_COLUMNS
    try:
        return app.output.get_size().columns
    except Exception:
        return DEFAULT_TERMINAL_COLUMNS


def prompt_line_width(cols: int | None = None) -> int:
    """Visible width for a live prompt-region line (last column left empty)."""
    width = terminal_columns() if cols is None else cols
    return max(width - 1, 1)


def prompt_text_width(text: str) -> int:
    """Terminal columns occupied by ``text`` after stripping controls.

    Counts display width, not code points: CJK and emoji are typically two
    columns, so a live row clipped by ``len()`` can still soft-wrap.
    """
    return get_cwidth(strip_terminal_controls(text))


def clip_prompt_text(text: str, max_len: int) -> str:
    """Truncate to ``max_len`` terminal columns after stripping controls."""
    visible = strip_terminal_controls(text)
    if max_len <= 0:
        return ""
    if prompt_text_width(visible) <= max_len:
        return visible
    budget = max_len - 1  # one column for the ellipsis
    clipped: list[str] = []
    used = 0
    for char in visible:
        width = get_cwidth(char)
        if used + width > budget:
            break
        clipped.append(char)
        used += width
    return "".join(clipped) + "…"


__all__ = [
    "DEFAULT_TERMINAL_COLUMNS",
    "clip_prompt_text",
    "prompt_line_width",
    "prompt_text_width",
    "terminal_columns",
]
