"""Compose the live permission and CI repair count line."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import ANSI, fragment_list_to_text, to_formatted_text

from config.constants.ci_fixes import CI_FIX_STATUS_LABEL
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.auto_status import auto_status_ansi
from surfaces.interactive_shell.ui.input_prompt.layout import prompt_line_width
from surfaces.shared.terminal.prompt_layout import prompt_text_width

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session


def prompt_status_ansi(session: Session, *, quiet: bool = False) -> str:
    """Place the cached repair count beside permissions when the terminal fits both."""
    count_fn = session.terminal.ci_fix_count_fn
    if count_fn is None:
        return auto_status_ansi(session, quiet=quiet)
    count = count_fn()
    chip = f"{CI_FIX_STATUS_LABEL} ({count})" + (" ✓" if count > 0 else "")
    width = prompt_line_width()
    auto = auto_status_ansi(session, quiet=quiet, max_width=width)
    # The model/effort and permission row is what this footer exists to show,
    # so the chip gives way before any of that content loses space. Never pad
    # to full width: resize reflow would strand stale chrome in scrollback.
    plain = fragment_list_to_text(to_formatted_text(ANSI(auto)))
    if prompt_text_width(plain + " · " + chip) > width:
        return auto
    style = ui_theme.SECONDARY_ANSI if count > 0 else ui_theme.DIM_ANSI
    return f"{auto}{ui_theme.DIM_ANSI} · {style}{chip}{ui_theme.ANSI_RESET}"
