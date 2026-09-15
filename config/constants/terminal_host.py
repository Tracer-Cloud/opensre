"""The terminal application hosting the process, as macOS reports it."""

from __future__ import annotations

TERM_PROGRAM_ENV = "TERM_PROGRAM"
APPLE_TERMINAL_PROGRAM = "Apple_Terminal"
BASH_EXPORTED_FUNCTION_ENV_PREFIX = "BASH_FUNC_"
WINDOWS_COMMAND_SHELL_ENV = "COMSPEC"

__all__ = [
    "APPLE_TERMINAL_PROGRAM",
    "BASH_EXPORTED_FUNCTION_ENV_PREFIX",
    "TERM_PROGRAM_ENV",
    "WINDOWS_COMMAND_SHELL_ENV",
]
