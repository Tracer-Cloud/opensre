"""Compose the live permission and CI repair count line."""

from __future__ import annotations

from typing import TYPE_CHECKING

from config.constants import CI_FIX_COUNT_LABEL
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.auto_status import auto_status_ansi
from surfaces.interactive_shell.ui.input_prompt.layout import prompt_line_width

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session


def prompt_status_ansi(session: Session, *, quiet: bool = False) -> str:
    """Place the cached repair count beside permissions when the terminal fits both."""
    count_fn = session.terminal.ci_fix_count_fn
    if count_fn is None:
        return auto_status_ansi(session, quiet=quiet)
    count = count_fn()
    chip = f"{CI_FIX_COUNT_LABEL} ({count})" + (" ✓" if count > 0 else "")
    width = prompt_line_width()
    remaining = width - len(chip) - 3
    if remaining < len("Auto (High)"):
        return auto_status_ansi(session, quiet=quiet)
    auto = auto_status_ansi(session, quiet=quiet, max_width=remaining)
    style = ui_theme.SECONDARY_ANSI if count > 0 else ui_theme.DIM_ANSI
    return f"{auto}{ui_theme.DIM_ANSI} · {style}{chip}{ui_theme.ANSI_RESET}"
