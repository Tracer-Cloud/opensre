"""Interactive input picker for ``opensre investigate`` when no input is provided."""

from __future__ import annotations

from typing import Any

import questionary
from rich.console import Console

from app.cli.alert_templates import build_alert_template
from app.cli.constants import SAMPLE_ALERT_OPTIONS
from app.cli.payload import load_file, load_interactive

_console = Console()

_STYLE = questionary.Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "bold"),
        ("answer", "fg:cyan bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green"),
    ]
)


def prompt_for_input() -> dict[str, Any]:
    """Show an interactive menu and return the chosen alert payload."""
    _console.print()
    _console.rule("[bold]OpenSRE · Investigation[/bold]")
    _console.print()

    mode = questionary.select(
        "How would you like to provide the alert?",
        choices=[
            questionary.Choice("Try a sample alert (no setup needed)", value="sample"),
            questionary.Choice("Paste alert JSON", value="paste"),
            questionary.Choice("Load from a file", value="file"),
        ],
        style=_STYLE,
    ).ask()

    if mode is None:
        raise SystemExit(0)

    if mode == "sample":
        return _pick_sample_alert()
    if mode == "paste":
        return load_interactive()
    if mode == "file":
        return _pick_file()

    raise SystemExit(0)


def _pick_sample_alert() -> dict[str, Any]:
    choice = questionary.select(
        "Pick a sample alert:",
        choices=[
            questionary.Choice(title=label, value=key) for key, label in SAMPLE_ALERT_OPTIONS
        ],
        style=_STYLE,
    ).ask()

    if choice is None:
        raise SystemExit(0)

    _console.print(f"\n[dim]Using sample alert:[/dim] [bold]{choice}[/bold]\n")
    return build_alert_template(choice)


def _pick_file() -> dict[str, Any]:
    path = questionary.path(
        "Path to alert file (.json, .md, .txt):",
        style=_STYLE,
    ).ask()

    if not path or not path.strip():
        raise SystemExit(0)

    return load_file(path.strip())
