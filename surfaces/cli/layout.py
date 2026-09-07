"""Rich landing and help renderers for the OpenSRE CLI."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import click
from rich.console import Console
from rich.text import Text

from infrastructure.terminal.theme import BRAND, DIM, TEXT

#: First-run actions only. Everything else is discoverable via ``opensre --help``;
#: a landing page that lists every command reads as "here is everything" rather
#: than "start here".
_LANDING_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("opensre setup", "Sign in to OpenSRE, activate the hosted model, then open the shell"),
    ('opensre ask "why is checkout-api slow?"', "Ask the agent a question directly"),
    ("opensre doctor", "Check this machine is set up correctly"),
    ("opensre --help", "See every command"),
)


#: Commands a new user needs, in the order they need them. Anything not listed
#: falls through to "More commands", so a newly added command is never hidden.
_GETTING_STARTED: tuple[str, ...] = (
    "setup",
    "onboard",
    "ask",
    "doctor",
    "health",
)

#: Day-to-day operation once configured.
_EVERYDAY: tuple[str, ...] = (
    "auth",
    "config",
    "integrations",
    "gateway",
    "cron",
    "update",
)


def _partition_commands(
    commands: Sequence[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the command list into getting-started, everyday, and the rest."""
    by_name = dict(commands)
    started = [(name, by_name[name]) for name in _GETTING_STARTED if name in by_name]
    everyday = [(name, by_name[name]) for name in _EVERYDAY if name in by_name]
    claimed = {name for name, _ in started + everyday}
    rest = [(name, help_text) for name, help_text in commands if name not in claimed]
    return started, everyday, rest


def _commands_from_group(group: click.Group) -> tuple[tuple[str, str], ...]:
    help_rows = getattr(group, "help_command_rows", None)
    if callable(help_rows):
        return cast("tuple[tuple[str, str], ...]", help_rows())
    ctx = click.Context(group)
    rows = []
    for name in group.list_commands(ctx):
        cmd = group.get_command(ctx, name)
        if cmd is not None and not cmd.hidden:
            rows.append((name, cmd.get_short_help_str(limit=200)))
    return tuple(rows)


def _options_from_command(command: click.Command) -> tuple[tuple[str, str], ...]:
    ctx = click.Context(command)
    rows: list[tuple[str, str]] = []
    for param in command.get_params(ctx):
        if getattr(param, "hidden", False):
            continue
        if not isinstance(param, click.Option):
            continue
        record = param.get_help_record(ctx)
        if record is not None:
            rows.append(record)
    return tuple(rows)


def _render_usage(console: Console) -> None:
    console.print(
        Text.assemble(
            ("  Usage: "),
            ("opensre", f"bold {TEXT}"),
            (" [OPTIONS] [COMMAND] [ARGS]..."),
        )
    )
    console.print(
        Text.assemble(
            ("  ", ""),
            ("No COMMAND", DIM),
            (": start the interactive shell when stdin/stdout are TTYs.", DIM),
        )
    )


def _render_rows(
    console: Console,
    *,
    title: str,
    rows: Sequence[tuple[str, str]],
    width: int | None = None,
) -> None:
    # ``width`` is the preferred column, not a cap: a label longer than it would
    # otherwise print unpadded and run straight into its description.
    longest_label = max((len(label) for label, _ in rows), default=0)
    effective_width = max(width + 2 if width is not None else 0, longest_label + 2)
    console.print(Text.assemble((f"  {title}:", f"bold {TEXT}")))
    for label, description in rows:
        console.print(
            Text.assemble(
                ("    ", ""),
                (f"{label:<{effective_width}}", f"bold {BRAND}"),
                description,
            )
        )


def render_help(group: click.Group) -> None:
    """Render the root help view from the command spec table (no command imports)."""
    console = Console(highlight=False)
    commands = _commands_from_group(group)
    options = _options_from_command(group)
    console.print()
    _render_usage(console)
    console.print()
    started, everyday, rest = _partition_commands(commands)
    _render_rows(console, title="Getting started", rows=started, width=16)
    console.print()
    _render_rows(console, title="Everyday", rows=everyday, width=16)
    console.print()
    _render_rows(console, title="More commands", rows=rest, width=16)
    console.print()
    _render_rows(console, title="Options", rows=options)
    console.print()


def render_landing(group: click.Group) -> None:
    """Render the root landing page shown with no subcommand."""
    from surfaces.shared.terminal.banner import build_launch_banner

    console = Console(highlight=False)
    options = _options_from_command(group)
    console.print()
    console.print(build_launch_banner(console))
    console.print(
        Text.assemble(
            ("  ", ""),
            "open-source SRE agent",
        )
    )
    console.print()
    _render_usage(console)
    console.print()
    _render_rows(console, title="Quick start", rows=_LANDING_EXAMPLES, width=42)
    console.print()
    _render_rows(console, title="Options", rows=options)
    console.print()


class RichGroup(click.Group):
    """Click group with a custom Rich-powered help screen."""

    def format_help(self, ctx: click.Context, _formatter: click.HelpFormatter) -> None:
        assert isinstance(ctx.command, click.Group)
        render_help(ctx.command)
