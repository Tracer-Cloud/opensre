"""Slash commands for interactive session mode."""

from __future__ import annotations

from rich.console import Console

from app.cli.session.banner import render_status
from app.cli.session.state import SessionState

_console = Console(highlight=False)


def handle_slash_command(text: str, state: SessionState) -> bool:
    command = text.strip()
    if command == "/help":
        _console.print("[bold cyan]Session commands[/bold cyan]")
        _console.print("  [cyan]/help[/cyan]         Show command reference")
        _console.print("  [cyan]/status[/cyan]       Show current session status")
        _console.print("  [cyan]/trust on|off[/cyan] Toggle trust mode")
        _console.print("  [cyan]/clear[/cyan]        Reset in-memory context")
        _console.print("  [cyan]/quit[/cyan]         Exit session")
        return False
    if command == "/status":
        render_status(state)
        return False
    if command in {"/clear", "/reset"}:
        state.last_alert = None
        state.last_result = None
        state.interruption_requested = False
        state.conversation.clear()
        _console.print("[green]Session context cleared.[/green]")
        return False
    if command.startswith("/trust"):
        parts = command.split()
        if len(parts) != 2 or parts[1] not in {"on", "off"}:
            _console.print("[yellow]Usage:[/yellow] /trust on|off")
            return False
        state.trust_mode = parts[1] == "on"
        _console.print(
            f"[green]Trust mode {'enabled' if state.trust_mode else 'disabled'}.[/green]"
        )
        return False
    if command in {"/quit", "/exit"}:
        return True
    _console.print("[red]Unknown command.[/red] Use [cyan]/help[/cyan].")
    return False
