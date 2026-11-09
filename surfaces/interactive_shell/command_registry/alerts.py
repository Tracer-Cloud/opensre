"""Slash command: /alerts — show alert listener status."""

from __future__ import annotations

from rich.console import Console

from core.domain.alerts.inbox import get_current_inbox
from infrastructure.terminal.theme import BOLD_BRAND, DIM, HIGHLIGHT, WARNING
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import print_repl_table, repl_table


def _cmd_alerts(_session: Session, console: Console, _args: list[str]) -> bool:
    inbox = get_current_inbox()
    if inbox is None:
        console.print(f"[{WARNING}]alert listener is not active.[/]")
        console.print(
            f"[{DIM}]Enable it with alert_listener_enabled: true in "
            "~/.opensre/config.yml (interactive section) or "
            "OPENSRE_ALERT_LISTENER_ENABLED=1, then restart the shell.[/]"
        )
        return True

    table = repl_table(title="Alert Inbox\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("status", f"[{HIGHLIGHT}]listening[/]")
    table.add_row("queue depth", str(inbox.qsize))
    table.add_row("dropped", str(inbox.dropped))

    for alert in inbox.peek_last(5):
        table.add_row(f"[{DIM}]recent[/]", f"{alert.alert_name or 'untitled'} — {alert.text[:80]}")

    print_repl_table(console, table)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand("/alerts", "Show alert listener status.", _cmd_alerts),
]

__all__ = ["COMMANDS"]
