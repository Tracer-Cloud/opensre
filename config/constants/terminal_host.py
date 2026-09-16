"""Terminal-host environment: the hosting app, shell hints, and the size/colour hints a child reads."""

from __future__ import annotations

TERM_PROGRAM_ENV = "TERM_PROGRAM"
APPLE_TERMINAL_PROGRAM = "Apple_Terminal"
BASH_EXPORTED_FUNCTION_ENV_PREFIX = "BASH_FUNC_"
WINDOWS_COMMAND_SHELL_ENV = "COMSPEC"

# Size hints Rich honours in a child whose stdout is a pipe (no TTY to measure).
TERMINAL_COLUMNS_ENV = "COLUMNS"
TERMINAL_LINES_ENV = "LINES"
# Rich short-circuits to 80x25 and ignores ``COLUMNS`` on these ``TERM`` values.
TERMINAL_TYPE_ENV = "TERM"
DUMB_TERMINAL_TYPES = frozenset({"dumb", "unknown"})
CAPABLE_TERMINAL_TYPE = "xterm-256color"
# Rich emits ANSI colour on a pipe only when told to.
FORCE_COLOR_ENV = "FORCE_COLOR"

__all__ = [
    "APPLE_TERMINAL_PROGRAM",
    "BASH_EXPORTED_FUNCTION_ENV_PREFIX",
    "CAPABLE_TERMINAL_TYPE",
    "DUMB_TERMINAL_TYPES",
    "FORCE_COLOR_ENV",
    "TERMINAL_COLUMNS_ENV",
    "TERMINAL_LINES_ENV",
    "TERMINAL_TYPE_ENV",
    "TERM_PROGRAM_ENV",
    "WINDOWS_COMMAND_SHELL_ENV",
]
