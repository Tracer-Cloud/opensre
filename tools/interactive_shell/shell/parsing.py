"""Shell command parsing for the interactive REPL.

Alpha mode: no command-safety policy
------------------------------------
While OpenSRE is in alpha we run **every** command the user or action agent
asks for. There is intentionally no allowlist, no read-only / mutating /
restricted classification, and no deny floor — guardrails are deliberately
omitted to keep developer velocity high. See
``docs/interactive-shell-action-policy.md`` for the rationale.

This module's only job is to turn command text into a shape the runner can
execute. A ``shell_run`` command is host-shell source, so every non-empty
command runs through the configured shell. This gives the action tool ordinary
shell semantics for quoting, expansions, redirection, and operators without
trying to reproduce shell parsing with partial regular expressions.

The REPL still handles a standalone ``cd`` or ``pwd`` itself so a directory
change persists across turns. That detection uses shell-aware tokenization only
to decide whether the input is one simple builtin; it never chooses how a
general command executes.

The only non-execution outcome is a ``parse_error`` for genuinely empty input
(e.g. a bare ``!``). That is input validation, not a safety guardrail.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

_EXPLICIT_SHELL_PREFIX = "!"
_POSIX_SHELL_SYNTAX = frozenset(";&|<>(){}$`#\n\r*?[")
_WINDOWS_SHELL_SYNTAX = frozenset("&|<>()%!\n\r")


@dataclass(frozen=True)
class ParsedShellCommand:
    """Structured command parsing result.

    ``use_shell`` is True for every non-empty command: ``shell_run.command`` is
    host-shell source. ``passthrough`` records only the explicit ``!`` prefix so
    the runner can surface the "shell passthrough" hint for it.
    """

    command: str
    argv: list[str] | None
    passthrough: bool
    use_shell: bool
    parse_error: str | None = None


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _split_builtin_argv(command: str, *, is_windows: bool) -> list[str] | None:
    """Tokenize one potential REPL builtin without losing quoted punctuation."""
    try:
        lexer = shlex.shlex(
            command,
            posix=not is_windows,
            punctuation_chars=True,
        )
        lexer.whitespace_split = True
        argv = list(lexer)
    except ValueError:
        return None

    if is_windows:
        argv = [_strip_outer_quotes(token) for token in argv]
    return argv


def _contains_shell_syntax(command: str, *, is_windows: bool) -> bool:
    """Return whether ``command`` has unquoted syntax a REPL builtin cannot own."""
    syntax = _WINDOWS_SHELL_SYNTAX if is_windows else _POSIX_SHELL_SYNTAX
    quote: str | None = None
    escaped = False

    for character in command:
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if character == "'":
                quote = None
            continue
        if quote == '"':
            if character == '"':
                quote = None
            elif character in {"$", "`"}:
                return True
            elif character == "\\":
                escaped = True
            continue
        if character == '"' or (character == "'" and not is_windows):
            quote = character
        elif (character == "\\" and not is_windows) or (character == "^" and is_windows):
            escaped = True
        elif character in syntax:
            return True

    return quote is not None


def parse_shell_command(command: str) -> ParsedShellCommand:
    """Parse command text into an executable shape (no safety policy applied)."""
    stripped = command.strip()

    if stripped.startswith(_EXPLICIT_SHELL_PREFIX):
        passthrough_command = stripped[len(_EXPLICIT_SHELL_PREFIX) :].strip()
        if not passthrough_command:
            return ParsedShellCommand(
                command="",
                argv=None,
                passthrough=True,
                use_shell=True,
                parse_error="missing command after passthrough prefix (!).",
            )
        return ParsedShellCommand(
            command=passthrough_command,
            argv=None,
            passthrough=True,
            use_shell=True,
        )

    if not stripped:
        return ParsedShellCommand(
            command=stripped,
            argv=None,
            passthrough=False,
            use_shell=False,
            parse_error="empty command.",
        )

    return ParsedShellCommand(
        command=stripped,
        argv=None,
        passthrough=False,
        use_shell=True,
    )


def argv_for_repl_builtin_detection(
    *, parsed: ParsedShellCommand, is_windows: bool
) -> list[str] | None:
    """Return argv when the command is one standalone ``cd`` or ``pwd`` builtin."""
    if not parsed.command.strip() or _contains_shell_syntax(
        parsed.command,
        is_windows=is_windows,
    ):
        return None

    argv = _split_builtin_argv(parsed.command.strip(), is_windows=is_windows)
    if argv is None or not argv or argv[0].lower() not in {"cd", "pwd"}:
        return None
    return argv


__all__ = [
    "ParsedShellCommand",
    "argv_for_repl_builtin_detection",
    "parse_shell_command",
]
