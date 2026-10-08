"""Non-interactive help renderers for the slash-command catalog.

The arrow-key picker lives in :mod:`help_browser`; these renderers serve
``/help <topic>`` and any non-TTY surface, where the catalog is printed rather
than browsed.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.shared.terminal.components.rendering import (
    print_repl_table,
    repl_print,
    repl_table,
)

HelpSection = tuple[str, Sequence[SlashCommand]]


def render_help_index(console: Console, sections: Sequence[HelpSection]) -> None:
    """Render the compact non-interactive help index."""
    table = repl_table(
        title="Slash commands",
        title_style=str(ui_theme.BOLD_BRAND),
        show_header=False,
    )
    table.add_column("command", no_wrap=True, min_width=18)
    table.add_column("description", style=str(ui_theme.DIM))

    for section_name, commands in sections:
        if not commands:
            continue
        table.add_row(f"[{ui_theme.BOLD_BRAND}]{escape(section_name)}[/]", "")
        for index, command in enumerate(commands):
            table.add_row(
                f"  [{ui_theme.HIGHLIGHT}]{escape(command.name)}[/]",
                escape(command.description),
                end_section=(index == len(commands) - 1),
            )

    print_repl_table(console, table)
    repl_print(
        console,
        f"[{ui_theme.DIM}]Use[/] [bold]/help <command>[/bold] [{ui_theme.DIM}]for usage.[/]",
    )


def render_section_detail(
    console: Console,
    section_name: str,
    commands: Sequence[SlashCommand],
) -> None:
    """Render one category using the same compact description-only style."""
    table = repl_table(
        title=f"{section_name} commands",
        title_style=str(ui_theme.BOLD_BRAND),
        show_header=False,
    )
    table.add_column("command", no_wrap=True, min_width=18)
    table.add_column("description", style=str(ui_theme.DIM))
    for command in commands:
        table.add_row(
            f"[{ui_theme.HIGHLIGHT}]{escape(command.name)}[/]",
            escape(command.description),
        )
    print_repl_table(console, table)
    repl_print(
        console,
        f"[{ui_theme.DIM}]Use[/] [bold]/help <command>[/bold] [{ui_theme.DIM}]for usage.[/]",
    )


def render_command_detail(console: Console, command: SlashCommand) -> None:
    """Render detailed help for one slash command."""
    table = Table(
        title=command.name,
        title_style=str(ui_theme.BOLD_BRAND),
        show_header=False,
        box=None,
    )
    table.add_column("label", style="bold", no_wrap=True)
    table.add_column("value")
    table.add_row("description", escape(command.description))

    if command.usage:
        table.add_row("usage", "\n".join(escape(item) for item in command.usage))
    if command.examples:
        table.add_row("examples", "\n".join(escape(item) for item in command.examples))
    if command.notes:
        table.add_row("notes", "\n".join(escape(item) for item in command.notes))

    print_repl_table(console, table)


def has_help_details(command: SlashCommand) -> bool:
    """True when a command carries usage, examples, or notes worth previewing."""
    return bool(command.usage or command.examples or command.notes)


__all__ = [
    "HelpSection",
    "has_help_details",
    "render_command_detail",
    "render_help_index",
    "render_section_detail",
]
