"""Slash command /tools."""

from __future__ import annotations

from rich.console import Console

from config.interactive_override import interactive_override_env
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import render_tools_table
from surfaces.interactive_shell.ui.tool_browser import browse_tools
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.tables.tool_catalog import build_tool_catalog


def _validate_tools_args(args: list[str]) -> str | None:
    return "Use /tools without arguments to browse registered tools." if args else None


def _cmd_tools(_session: Session, console: Console, _args: list[str]) -> bool:
    entries = build_tool_catalog()
    if (
        entries
        and repl_tty_interactive()
        and console.is_terminal
        and not interactive_override_env()
    ):
        browse_tools(entries)
    else:
        render_tools_table(console, entries)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/tools",
        "Browse registered tools and their descriptions.",
        _cmd_tools,
        usage=("/tools",),
        validate_args=_validate_tools_args,
    )
]

__all__ = ["COMMANDS", "_cmd_tools"]
