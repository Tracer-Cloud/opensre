"""Model, applied effort, and permissions below the prompt input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.constants.repl_autonomy import (
    AUTO_LEVEL_BAR_CAPTIONS,
    DEFAULT_AUTO_LEVEL,
    format_auto_status_bar,
)
from config.llm_reasoning_effort import applied_reasoning_effort
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text, prompt_line_width
from surfaces.shared.terminal.prompt_layout import prompt_text_width

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session

# Gutter that aligns this row's text with the spinner row above (two columns of
# indent plus the glyph and its space). Public because a caller passing
# ``max_width`` has to reserve it before deciding what else fits on the row.
STATUS_TEXT_INDENT = "    "


def auto_status_ansi(session: Session, *, quiet: bool = False, max_width: int | None = None) -> str:
    """Model first, with truthful effort and permission copy on one bounded row.

    Permission copy stays visible at every level, including High (the default).
    The model clips before applied effort and permissions on narrow terminals.
    """
    level = getattr(session.terminal, "auto_level", DEFAULT_AUTO_LEVEL)
    provider, model = session.terminal.provider_model
    width = prompt_line_width() if max_width is None else max_width
    if provider == "unknown":
        clipped = clip_prompt_text(f"{STATUS_TEXT_INDENT}{format_auto_status_bar(level)}", width)
        title, separator, caption = clipped.partition(" · ")
        rest = separator + caption
    else:
        permission = AUTO_LEVEL_BAR_CAPTIONS[level]
        effort = applied_reasoning_effort(provider, model, session.reasoning_effort)
        effort_label = f" ({effort.title()})" if effort else ""
        rest = f"{effort_label} · {permission}"
        title_budget = width - prompt_text_width(STATUS_TEXT_INDENT + rest)
        if title_budget < 1:
            # At extreme widths, permissions take precedence over route metadata.
            title = ""
            rest = clip_prompt_text(f"{STATUS_TEXT_INDENT}{permission}", width)
        else:
            title = STATUS_TEXT_INDENT + clip_prompt_text(
                f"{provider} / {model or 'CLI default'}", title_budget
            )
    title_ansi = ui_theme.DIM_ANSI if quiet else ui_theme.BOLD_REPLY_MARKER_ANSI
    # Keep live content shorter than the terminal. Internal padding before the
    # CI chip turns this into a full-width row that reflows into scrollback on
    # a shrink before the resize callback can replace it.
    rest_ansi = ui_theme.DIM_ANSI if quiet else ui_theme.SECONDARY_ANSI
    return f"{title_ansi}{title}{ui_theme.ANSI_RESET}{rest_ansi}{rest}{ui_theme.ANSI_RESET}"


__all__ = ["auto_status_ansi"]
