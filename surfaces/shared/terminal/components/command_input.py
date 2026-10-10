"""Transient, keyboard-accessible command input surfaces."""

from __future__ import annotations

from prompt_toolkit.application import Application
from prompt_toolkit.styles import Style

import infrastructure.terminal.theme as theme
from surfaces.shared.terminal.components.choice_menu import (
    enter_inline_menu,
    leave_inline_menu,
    repl_tty_interactive,
)


def command_input_style() -> Style:
    """Use the active terminal theme for input surfaces."""
    return Style.from_dict(
        {
            "": f"{theme.TEXT} bg:{theme.INPUT_SURFACE}",
            "frame.border": str(theme.HIGHLIGHT),
            "frame.label": str(theme.HIGHLIGHT),
            "selected": f"{theme.TEXT} bg:{theme.menu_selection_hex()}",
            "description": str(theme.SECONDARY),
            "hint": str(theme.DIM),
            "error": str(theme.ERROR),
            "text-area": f"{theme.TEXT} bg:{theme.INPUT_SURFACE}",
        }
    )


def run_command_input[Result](app: Application[Result]) -> Result | None:
    """Run an exclusively owned input surface without leaving menu paint behind."""
    if not repl_tty_interactive():
        return None
    enter_inline_menu()
    try:
        return app.run()
    finally:
        leave_inline_menu()
