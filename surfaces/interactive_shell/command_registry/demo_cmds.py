"""``/demo``: reopen the first-experience demo picker on demand."""

from __future__ import annotations

from rich.console import Console

from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.startup.demo_picker import offer_demo
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive


def _cmd_demo(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not repl_tty_interactive():
        console.print("The demo picker needs an interactive terminal.")
        return True
    offer_demo(session, console, force=True)
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/demo",
        "Pick a guided demo that runs on real repositories from this machine.",
        _cmd_demo,
        usage=("/demo",),
        # Opens read-only pickers; the queued demo turn is gated on its own.
        mutating=False,
    ),
]

__all__ = ["COMMANDS"]
