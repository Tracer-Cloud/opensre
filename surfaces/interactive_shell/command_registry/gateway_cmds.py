"""Slash command: /gateway — control the OpenSRE gateway daemon."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from gateway.core.process import (
    GATEWAY_LOG_FILE,
    gateway_daemon_pid,
    read_component_status,
    read_gateway_log_tail,
    start_gateway_daemon,
    stop_gateway_daemon,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT
from surfaces.shared.gateway_entrypoint import gateway_entry_argv

_USAGE = "/gateway [start|stop|status|logs [lines]]"


def _print_outcome(console: Console, ok: bool, message: str) -> None:
    console.print(f"[{HIGHLIGHT if ok else ERROR}]{escape(message)}[/]")


def _cmd_gateway(_session: Session, console: Console, args: list[str]) -> bool:
    sub = args[0].lower() if args else "status"

    if sub == "start":
        _print_outcome(console, *start_gateway_daemon(argv=gateway_entry_argv()))
        console.print(f"[{DIM}]logs: {GATEWAY_LOG_FILE} — /gateway logs to tail[/]")
    elif sub == "stop":
        _print_outcome(console, *stop_gateway_daemon())
    elif sub == "status":
        pid = gateway_daemon_pid()
        state = f"[{HIGHLIGHT}]running (pid {pid})[/]" if pid else f"[{DIM}]stopped[/]"
        console.print(f"OpenSRE gateway: {state}")
        for name, detail in read_component_status().items():
            console.print(f"  {escape(name)}: {escape(detail)}")
        console.print(f"[{DIM}]logs: {GATEWAY_LOG_FILE}[/]")
    elif sub == "logs":
        lines = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
        if tail := read_gateway_log_tail(lines):
            console.print(escape(tail))
        else:
            console.print(f"[{DIM}]no gateway logs yet at {GATEWAY_LOG_FILE}[/]")
    else:
        console.print(f"[{ERROR}]usage:[/] {_USAGE}")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/gateway",
        "Control the background OpenSRE gateway daemon: start, stop, status, logs.",
        _cmd_gateway,
        usage=(_USAGE,),
        notes=(
            "The gateway daemon runs the web health app, Telegram chat, and the "
            "task scheduler; logs are stored in ~/.opensre/gateway/gateway.log.",
        ),
        first_arg_completions=(
            ("start", "start the gateway daemon (web, telegram, scheduler)"),
            ("stop", "stop the gateway daemon"),
            ("status", "show the daemon and its components"),
            ("logs", "print recent gateway log lines"),
        ),
    ),
]

__all__ = ["COMMANDS"]
