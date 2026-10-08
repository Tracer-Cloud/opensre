"""Slash commands: history management and privacy controls (/history, /privacy)."""

from __future__ import annotations

from prompt_toolkit.history import FileHistory, InMemoryHistory
from rich.console import Console
from rich.markup import escape

from core.agent_harness.spi.session_state import exclusive_stdin_active
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.prompt_history import (
    clear_persisted_history,
    load_command_history_entries,
    prompt_history_path,
)
from surfaces.interactive_shell.prompt_history.policy import (
    DEFAULT_REDACTION_RULES,
    RedactingFileHistory,
)
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    print_repl_table,
    repl_table,
)
from surfaces.shared.terminal.components.choice_menu import (
    CRUMB_SEP,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from surfaces.shared.terminal.components.subcommand_menu import repl_choose_subcommand


def _show_history(console: Console) -> bool:
    entries = load_command_history_entries()
    if not entries:
        console.print(f"[{DIM}]no history yet.[/]")
        return True

    table = repl_table(title="Command history\n", title_style=BOLD_BRAND)
    table.add_column("#", style=DIM, justify="right")
    table.add_column("text", overflow="fold")

    for i, entry in enumerate(entries, start=1):
        table.add_row(str(i), escape(entry))
    print_repl_table(console, table)
    return True


def _history_clear(session: Session, console: Console) -> bool:  # noqa: ARG001
    if clear_persisted_history():
        console.print(
            f"[{HIGHLIGHT}]cleared[/] persistent history. Up-arrow recall resets on next launch."
        )
    else:
        console.print(
            f"[{ERROR}]could not clear history[/] (file system error). "
            f"path: {prompt_history_path()}"
        )
    return True


def _history_pause(session: Session, console: Console, *, paused: bool) -> bool:
    backend = session.terminal.prompt_history_backend
    if isinstance(backend, RedactingFileHistory):
        backend.paused = paused
        state = "off" if paused else "on"
        console.print(f"[{DIM}]history persistence is now {state} for this session.[/]")
        return True
    if isinstance(backend, FileHistory):
        if paused:
            console.print(
                f"[{DIM}]history is persisting to disk without redaction. "
                "Restart with OPENSRE_HISTORY_REDACT=1 to enable runtime pause support, "
                "or OPENSRE_HISTORY_ENABLED=0 to disable persistence entirely.[/]"
            )
            return True
        console.print(f"[{DIM}]history persistence is already on (raw file history).[/]")
        return True
    if backend is None or isinstance(backend, InMemoryHistory):
        if paused:
            console.print(f"[{DIM}]history is already not persisting in this session.[/]")
            return True
        console.print(
            f"[{DIM}]history is in-memory only. "
            "Restart with OPENSRE_HISTORY_ENABLED=1 to enable persistence.[/]"
        )
        return True
    if paused:
        console.print(f"[{DIM}]history is already not persisting in this session.[/]")
        return True
    console.print(
        f"[{DIM}]history backend does not support runtime persistence controls in this session.[/]"
    )
    return True


_RETENTION_CAPS: tuple[str, ...] = ("100", "500", "1000", "5000")


_RETENTION_CUSTOM_LABEL = "enter a number"


def _prompt_retention_cap() -> str | None:
    """Ask for a retention cap; ``None`` when the picker is dismissed.

    The presets cover the common caps; the last row types any other number in
    place, so the picker never blocks a value ``/history retention <N>`` allows.
    """
    return repl_choose_one(
        title="retention cap",
        breadcrumb=f"/history{CRUMB_SEP}retention",
        choices=[(cap, cap) for cap in _RETENTION_CAPS]
        + [(_RETENTION_CUSTOM_LABEL, _RETENTION_CUSTOM_LABEL)],
        custom_label=_RETENTION_CUSTOM_LABEL,
    )


def _history_retention(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
        # A turn that owns stdin asks for the cap rather than closing on usage.
        if repl_tty_interactive() and exclusive_stdin_active(session):
            cap = _prompt_retention_cap()
            if cap is None:
                return True
            args = [cap]
        else:
            console.print(f"[{ERROR}]usage:[/] /history retention <N>")
            return True
    try:
        n = int(args[0])
        if n < 0:
            raise ValueError
    except ValueError:
        console.print(f"[{ERROR}]retention must be a non-negative integer[/]")
        return True

    backend = session.terminal.prompt_history_backend
    if isinstance(backend, RedactingFileHistory):
        backend.set_max_entries(n, prune=True)
        console.print(
            f"[{DIM}]retention cap set to {n} for this session "
            "(set OPENSRE_HISTORY_MAX_ENTRIES or interactive.history.max_entries to persist).[/]"
        )
        return True
    console.print(
        f"[{DIM}]retention applies only when redacting persistent history. "
        "Restart with OPENSRE_HISTORY_REDACT=1 to enable.[/]"
    )
    return True


_HISTORY_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("show", "print the persisted history"),
    ("clear", "delete persisted history file"),
    ("off", "pause history persistence for this session"),
    ("on", "resume history persistence for this session"),
    ("retention", "set max entries cap (e.g. /history retention 1000)"),
)


def _interactive_history_menu(session: Session, console: Console) -> bool:
    root = "/history"
    while True:
        sub = repl_choose_subcommand(parent=root, options=_HISTORY_FIRST_ARGS)
        if sub is None:
            return True
        show_section_break = False
        if sub == "show":
            _show_history(console)
            show_section_break = True
        elif sub == "clear":
            _history_clear(session, console)
            show_section_break = True
        elif sub == "off":
            _history_pause(session, console, paused=True)
            show_section_break = True
        elif sub == "on":
            _history_pause(session, console, paused=False)
            show_section_break = True
        elif sub == "retention":
            # One owner for the cap prompt: the handler asks when none is given.
            show_section_break = _history_retention(session, console, [])
        if show_section_break:
            repl_section_break(console)


def _cmd_history(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_history_menu(session, console)

    if not args:
        return _show_history(console)

    sub = args[0].lower()
    if sub == "show":
        return _show_history(console)
    if sub == "clear":
        return _history_clear(session, console)
    if sub == "off":
        return _history_pause(session, console, paused=True)
    if sub == "on":
        return _history_pause(session, console, paused=False)
    if sub == "retention":
        return _history_retention(session, console, args[1:])

    console.print(f"[{ERROR}]usage:[/] /history [show|clear|off|on|retention <N>]")
    return True


def _cmd_privacy(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    backend = session.terminal.prompt_history_backend
    table = repl_table(title="Privacy settings\n", title_style=BOLD_BRAND, show_header=False)
    table.add_column("setting", style="bold")
    table.add_column("value")

    if isinstance(backend, RedactingFileHistory):
        persistence = "off (paused)" if backend.paused else "on"
        redaction = "on"
        retention = str(backend.max_entries) if backend.max_entries > 0 else "unlimited"
    elif backend is None or isinstance(backend, InMemoryHistory):
        persistence = "off (in-memory only)"
        redaction = "n/a"
        retention = "n/a"
    elif isinstance(backend, FileHistory):
        persistence = "on (no redaction)"
        redaction = "off"
        retention = "n/a"
    else:
        persistence = "unknown"
        redaction = "unknown"
        retention = "n/a"

    table.add_row("persistence", persistence)
    table.add_row("redaction", redaction)
    table.add_row("retention cap", retention)
    table.add_row("file", str(prompt_history_path()))
    table.add_row("built-in patterns", str(len(DEFAULT_REDACTION_RULES)))
    print_repl_table(console, table)
    console.print(
        f"[{DIM}]threat model: prompt history is stored unencrypted on disk. "
        f"Use[/] [{HIGHLIGHT}]/history clear[/] [{DIM}]after sharing your machine, "
        f"or[/] [{HIGHLIGHT}]/history off[/] [{DIM}]to pause this session, "
        f"or set OPENSRE_HISTORY_ENABLED=0 to disable persistence entirely.[/]"
    )
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/history",
        "Manage command history.",
        _cmd_history,
        usage=(
            "/history",
            "/history clear",
            "/history off",
            "/history on",
            "/history retention <N>",
        ),
        notes=("In a TTY, bare /history opens an interactive menu.",),
        first_arg_completions=_HISTORY_FIRST_ARGS,
    ),
    SlashCommand(
        "/privacy",
        "Show history persistence, redaction status, and threat model.",
        _cmd_privacy,
    ),
]

__all__ = ["COMMANDS"]
