"""Auto (Med) status line above the prompt input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.constants.repl_autonomy import DEFAULT_AUTO_LEVEL, format_auto_status_bar
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text, prompt_line_width

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session


def auto_status_ansi(session: Session, *, quiet: bool = False, max_width: int | None = None) -> str:
    """``Auto (High) · Allow all`` — idle gold, DIM while Thinking owns the accent.

    Permission copy stays visible at every level, including High (the default).
    The model id lives on ``/model`` and ``?``, not this chrome.
    """
    level = getattr(session.terminal, "auto_level", DEFAULT_AUTO_LEVEL)
    left = format_auto_status_bar(level)
    width = prompt_line_width() if max_width is None else max_width
    clipped = clip_prompt_text(left, width)
    title_end = clipped.find(" · ")
    title = clipped if title_end < 0 else clipped[:title_end]
    rest = "" if title_end < 0 else clipped[title_end:]
    pad = max(0, width - len(clipped))
    title_ansi = ui_theme.DIM_ANSI if quiet else ui_theme.BOLD_REPLY_MARKER_ANSI
    return (
        f"{title_ansi}{title}{ui_theme.ANSI_RESET}"
        f"{ui_theme.DIM_ANSI}{rest}{ui_theme.ANSI_RESET}"
        f"{' ' * pad}"
    )


__all__ = ["auto_status_ansi"]
