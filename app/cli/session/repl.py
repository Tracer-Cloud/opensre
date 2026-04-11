"""Persistent REPL runtime for OpenSRE terminal mode."""

from __future__ import annotations

import json

from rich.console import Console

from app.cli.errors import OpenSREError
from app.cli.session.banner import render_banner, render_run_summary
from app.cli.session.commands import handle_slash_command
from app.cli.session.router import route_input
from app.cli.session.run_manager import run_alert_investigation, run_followup
from app.cli.session.state import SessionState

_console = Console(highlight=False)


def _read_user_input(*, base_prompt: str) -> str:
    """Read one command from stdin, supporting multiline JSON paste."""
    first = input(base_prompt)
    stripped = first.lstrip()
    if not stripped.startswith("{"):
        return first

    buffer = first
    for _ in range(64):
        try:
            parsed = json.loads(buffer)
        except json.JSONDecodeError:
            next_line = input("... ")
            if next_line == "":
                return buffer
            buffer = f"{buffer}\n{next_line}"
            continue
        if isinstance(parsed, dict):
            return buffer
        return first
    return buffer


def run_repl_session() -> int:
    state = SessionState()
    render_banner(state)

    while True:
        try:
            trust = "trust:on" if state.trust_mode else "trust:off"
            raw = _read_user_input(base_prompt=f"opensre[{trust}]> ")
        except EOFError:
            return 0
        except KeyboardInterrupt:
            if state.active_run:
                state.interruption_requested = True
                state.active_run = False
            else:
                state.interruption_requested = False
            _console.print("\n[dim]Interrupted. Session still running.[/dim]")
            continue

        routed = route_input(raw)
        if routed.kind == "empty":
            continue
        if routed.kind == "slash":
            if handle_slash_command(routed.text, state):
                return 0
            continue

        try:
            if routed.kind == "alert" and routed.payload is not None:
                with _console.status("[cyan]Investigating alert...[/cyan]", spinner="dots"):
                    run_alert_investigation(state, routed.payload)
                render_run_summary(state)
            else:
                with _console.status("[cyan]Refining investigation...[/cyan]", spinner="dots"):
                    run_followup(state, routed.text)
                render_run_summary(state)
        except KeyboardInterrupt:
            state.interruption_requested = True
            state.active_run = False
            _console.print(
                "\n[yellow]Interrupted.[/yellow] Investigation canceled; session preserved."
            )
        except OpenSREError as exc:
            exc.show()
        except Exception as exc:  # noqa: BLE001
            _console.print(f"[red]Error:[/red] {type(exc).__name__}: {exc}")
