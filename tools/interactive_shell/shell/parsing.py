"""Shell command normalization for the interactive REPL.

Alpha mode intentionally applies no command-safety policy. Command syntax is
interpreted by the host shell rather than duplicated here; this module only
normalizes input and preserves the optional explicit ``!`` prefix.
"""

from __future__ import annotations

from dataclasses import dataclass

_EXPLICIT_SHELL_PREFIX = "!"


@dataclass(frozen=True)
class ParsedShellCommand:
    """Normalized shell command text and input-validation state."""

    command: str
    passthrough: bool
    parse_error: str | None = None


def parse_shell_command(command: str) -> ParsedShellCommand:
    """Normalize command text without attempting to parse shell grammar."""
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
