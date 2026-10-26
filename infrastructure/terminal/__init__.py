"""Shared terminal presentation primitives (theme, prompts, error rendering).

Package import stays cheap: prompt_toolkit lives behind :mod:`prompt_support` and
is only loaded when a caller asks for those names (or imports that submodule).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from infrastructure.terminal.errors import render_error
    from infrastructure.terminal.prompt_support import (
        CTRL_C_DOUBLE_PRESS_WINDOW_S,
        handle_ctrl_c_press,
        install_questionary_ctrl_c_double_exit,
        install_questionary_escape_cancel,
        repl_prompt_note_ctrl_c,
        repl_reset_ctrl_c_gate,
    )

__all__ = [
    "CTRL_C_DOUBLE_PRESS_WINDOW_S",
    "handle_ctrl_c_press",
    "install_questionary_ctrl_c_double_exit",
    "install_questionary_escape_cancel",
    "render_error",
    "repl_prompt_note_ctrl_c",
    "repl_reset_ctrl_c_gate",
]

_PROMPT_EXPORTS = frozenset(
    {
        "CTRL_C_DOUBLE_PRESS_WINDOW_S",
        "handle_ctrl_c_press",
        "install_questionary_ctrl_c_double_exit",
        "install_questionary_escape_cancel",
        "repl_prompt_note_ctrl_c",
        "repl_reset_ctrl_c_gate",
    }
)


def __getattr__(name: str) -> Any:
    if name == "render_error":
        from infrastructure.terminal.errors import render_error

        return render_error
    if name in _PROMPT_EXPORTS:
        from infrastructure.terminal import prompt_support

        return getattr(prompt_support, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
