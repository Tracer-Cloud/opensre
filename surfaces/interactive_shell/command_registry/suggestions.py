"""Small helpers for human-friendly slash-command suggestions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Literal

from surfaces.interactive_shell.command_registry.types import SlashCommand

SlashOutcome = Literal["unknown_command", "invalid_subcommand"]

_RICH_TAG_RE = re.compile(r"\[[^\]]+\]")


@dataclass(frozen=True, slots=True)
class LiteralSlashTypo:
    """A user-typed literal slash line that should not run."""

    message: str
    outcome: SlashOutcome


def closest_choice(value: str, choices: list[str] | tuple[str, ...]) -> str | None:
    """Return the nearest command-like choice for a typo, if confidence is high enough."""
    normalized = value.strip().lower()
    if not normalized:
        return None
    matches = get_close_matches(normalized, choices, n=1, cutoff=0.72)
    return matches[0] if matches else None


def subcommand_hints(cmd: SlashCommand) -> tuple[str, ...]:
    """Return enumerable first-argument keywords for ``cmd``.

    Only ``first_arg_completions`` are used — usage strings often contain
    free-form placeholders like ``<session-id-prefix>`` that must not be
    treated as literal subcommands.
    """
    return tuple(sorted({keyword.lower() for keyword, _label in cmd.first_arg_completions}))


def format_unknown_slash_message(
    command_line: str,
    *,
    command_names: tuple[str, ...],
) -> str:
    """Plain-text guidance for an unknown slash command root."""
    stripped = command_line.strip()
    name = stripped.split()[0] if stripped else stripped
    suggestion = closest_choice(name, command_names)
    if suggestion:
        return (
            f"Unknown command: {name}. "
            f"Did you mean {suggestion}? "
            "Type /help for the full command list."
        )
    return f"Unknown command: {name}. Type /help for the full command list."


def format_invalid_subcommand_message(
    cmd: SlashCommand,
    args: list[str],
) -> str:
    """Plain-text guidance for an invalid subcommand on a known slash command."""
    subcommand = args[0] if args else ""
    hints = subcommand_hints(cmd)
    if hints:
        choices_text = ", ".join(f"{cmd.name} {hint}" for hint in hints)
        return (
            f"Invalid subcommand: {subcommand}. "
            f"Try one of: {choices_text}. "
            f"Type /help for the full command list."
        )
    return f"Invalid subcommand: {subcommand}. Type /help for the full command list."


def resolve_literal_slash_typo(
    command_line: str,
    registry: dict[str, SlashCommand],
) -> LiteralSlashTypo | None:
    """Return typo guidance when a user-typed literal slash line should not run."""
    stripped = command_line.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped.split()
    if not parts:
        return None

    name = parts[0].lower()
    args = parts[1:]
    command_names = tuple(registry)

    cmd = registry.get(name)
    if cmd is None:
        return LiteralSlashTypo(
            message=format_unknown_slash_message(stripped, command_names=command_names),
            outcome="unknown_command",
        )

    if cmd.validate_args is not None:
        validation_error = cmd.validate_args(args)
        if validation_error is not None and args:
            hints = subcommand_hints(cmd)
            message = (
                format_invalid_subcommand_message(cmd, args)
                if hints and args[0].lower() not in hints
                else validation_error
            )
            return LiteralSlashTypo(
                message=message,
                outcome="invalid_subcommand",
            )

    return None


def strip_rich_markup(text: str) -> str:
    """Best-effort plain text for analytics payloads sourced from Rich strings."""
    return _RICH_TAG_RE.sub("", text).strip()


__all__ = [
    "LiteralSlashTypo",
    "SlashOutcome",
    "closest_choice",
    "format_invalid_subcommand_message",
    "format_unknown_slash_message",
    "resolve_literal_slash_typo",
    "strip_rich_markup",
    "subcommand_hints",
]
