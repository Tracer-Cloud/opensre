"""Auto (Med) status line above the prompt input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.constants.repl_autonomy import DEFAULT_AUTO_LEVEL, format_auto_status_bar
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text, prompt_line_width

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session

# Gutter that aligns this row's text with the spinner row above (two columns of
# indent plus the glyph and its space). Public because a caller passing
# ``max_width`` has to reserve it before deciding what else fits on the row.
STATUS_TEXT_INDENT = "    "


def auto_status_ansi(session: Session, *, quiet: bool = False, max_width: int | None = None) -> str:
    """``Auto (High) · Allow all`` — idle gold, DIM while Thinking owns the accent.

    Permission copy stays visible at every level, including High (the default).
    The model id lives on ``/model`` and ``?``, not this chrome.
    """
    level = getattr(session.terminal, "auto_level", DEFAULT_AUTO_LEVEL)
    left = f"{STATUS_TEXT_INDENT}{format_auto_status_bar(level)}"
    width = prompt_line_width() if max_width is None else max_width
    clipped = clip_prompt_text(left, width)
    title_end = clipped.find(" · ")
    title = clipped if title_end < 0 else clipped[:title_end]
    rest = "" if title_end < 0 else clipped[title_end:]
    title_ansi = ui_theme.DIM_ANSI if quiet else ui_theme.BOLD_REPLY_MARKER_ANSI
    # Keep live content shorter than the terminal. Internal padding before the
    # CI chip turns this into a full-width row that reflows into scrollback on
    # a shrink before the resize callback can replace it.
    return f"{title_ansi}{title}{ui_theme.ANSI_RESET}{ui_theme.DIM_ANSI}{rest}{ui_theme.ANSI_RESET}"


__all__ = ["auto_status_ansi"]
