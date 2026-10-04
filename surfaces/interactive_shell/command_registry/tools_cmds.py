"""Slash command /tools."""

from __future__ import annotations

from rich.console import Console

from surfaces.interactive_shell.command_registry.types import (
    SlashCommand,
    make_list_root_handler,
)
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import render_tools_table
from surfaces.interactive_shell.ui.tool_picker import browse_tools
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.tables.tool_catalog import build_tool_catalog


def _list_tools(_session: Session, console: Console, _args: list[str]) -> bool:
    entries = build_tool_catalog()
    if not entries or not repl_tty_interactive():
        render_tools_table(console, entries)
        return True
    browse_tools(entries)
    return True


_cmd_tools = make_list_root_handler(
    "/tools",
    _list_tools,
    list_aliases=("list", "ls", "tool", "tools"),
)

_TOOLS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list registered tools (chat + action surfaces)"),
    ("ls", "alias for list"),
    ("tool", "alias for list"),
    ("tools", "alias for list"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/tools",
        "List registered tools.",
        _cmd_tools,
        usage=("/tools", "/tools list"),
        first_arg_completions=_TOOLS_FIRST_ARGS,
    )
]

__all__ = ["COMMANDS", "_TOOLS_FIRST_ARGS", "_cmd_tools"]
