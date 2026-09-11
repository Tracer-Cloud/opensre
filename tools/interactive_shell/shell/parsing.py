"""Input normalization for host-shell commands.

``shell_run.command`` is always host-shell source. This module deliberately
does not interpret shell syntax; the configured platform shell is the single
syntax authority. Parsing here is limited to the explicit ``!`` prefix and
empty-input validation.
"""

from __future__ import annotations

from dataclasses import dataclass

_EXPLICIT_SHELL_PREFIX = "!"


@dataclass(frozen=True)
class ParsedShellCommand:
    """Normalized shell source plus explicit-passthrough metadata."""

    command: str
    passthrough: bool
    parse_error: str | None = None


def parse_shell_command(command: str) -> ParsedShellCommand:
    """Normalize shell source without attempting to tokenize its language."""
    stripped = command.strip()

    if stripped.startswith(_EXPLICIT_SHELL_PREFIX):
        passthrough_command = stripped[len(_EXPLICIT_SHELL_PREFIX) :].strip()
        if not passthrough_command:
            return ParsedShellCommand(
                command="",
                passthrough=True,
                parse_error="missing command after passthrough prefix (!).",
            )
        return ParsedShellCommand(command=passthrough_command, passthrough=True)

    if not stripped:
        return ParsedShellCommand(
            command="",
            passthrough=False,
            parse_error="empty command.",
        )

    return ParsedShellCommand(command=stripped, passthrough=False)


__all__ = ["ParsedShellCommand", "parse_shell_command"]
