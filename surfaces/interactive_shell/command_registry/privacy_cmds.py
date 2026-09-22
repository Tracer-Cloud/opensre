"""Slash commands: history management and privacy controls (/history, /privacy)."""

from __future__ import annotations

from prompt_toolkit.history import FileHistory, InMemoryHistory
from rich.console import Console
from rich.markup import escape

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
from surfaces.shared.terminal.components.detail_panel import repl_show_details


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


def _history_retention(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
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


def _history_menu_summary(session: Session) -> str:
    backend = session.terminal.prompt_history_backend
    if isinstance(backend, RedactingFileHistory):
        state = "Paused" if backend.paused else "Recording"
        cap = str(backend.max_entries) if backend.max_entries else "unlimited"
        return f"{state} · redacted · retention: {cap}"
    if isinstance(backend, FileHistory):
        return "Recording · unredacted · runtime pause unavailable"
    return "Not recording to disk"


def _interactive_history_menu(session: Session, console: Console) -> bool:
    root = "/history"
    initial = "show"
    while True:
        sub = repl_choose_one(
            title="History",
            breadcrumb=root,
            panel=True,
            initial_value=initial,
            note=_history_menu_summary(session),
            choices=[
                ("show", "View saved history ›"),
                ("off", "Pause recording"),
                ("on", "Resume recording"),
                ("retention", "Retention limit ›"),
                ("clear", "Clear saved history ›"),
                ("done", "Done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        initial = sub
        show_section_break = False
        if sub == "show":
            entries = load_command_history_entries()
            repl_show_details(
                title="History › Saved commands",
                fields=[(str(i), entry) for i, entry in enumerate(entries, start=1)],
                note="No saved history yet." if not entries else "",
            )
        elif sub == "clear":
            confirmed = repl_choose_one(
                title="Clear saved history?",
                breadcrumb=f"{root}{CRUMB_SEP}Clear",
                panel=True,
                initial_value="cancel",
                note="Deletes saved command history. Up-arrow recall resets on next launch.",
                choices=[("cancel", "Cancel"), ("clear", "Clear saved history")],
            )
            if confirmed == "clear":
                _history_clear(session, console)
                show_section_break = True
        elif sub in {"off", "on"}:
            _history_pause(session, console, paused=sub == "off")
            show_section_break = True
        elif sub == "retention":
            backend = session.terminal.prompt_history_backend
            if not isinstance(backend, RedactingFileHistory):
                repl_show_details(
                    title="History › Retention",
                    fields=[],
                    note="Runtime retention requires redacting file history. "
                    "Restart with OPENSRE_HISTORY_REDACT=1 to enable it.",
                )
                continue
            current = str(backend.max_entries)
            choices = [
                (str(n), "Unlimited" if n == 0 else f"{n} commands")
                for n in sorted({0, 100, 500, 1000, 5000, backend.max_entries})
            ]
            cap = repl_choose_one(
                title="Retention limit",
                breadcrumb=f"{root}{CRUMB_SEP}Retention",
                panel=True,
                current_value=current,
                initial_value=current,
                note="Session only. Enter applies and prunes older saved commands.",
                choices=choices,
            )
            if cap is not None:
                _history_retention(session, console, [cap])
                show_section_break = True
        if show_section_break:
            repl_section_break(console)


def _cmd_history(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_history_menu(session, console)

    if not args:
        return _show_history(console)

    sub = args[0].lower()
    if sub == "clear":
        return _history_clear(session, console)
    if sub == "off":
        return _history_pause(session, console, paused=True)
    if sub == "on":
        return _history_pause(session, console, paused=False)
    if sub == "retention":
        return _history_retention(session, console, args[1:])

    console.print(f"[{ERROR}]usage:[/] /history [clear|off|on|retention <N>]")
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


_HISTORY_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("clear", "delete persisted history file"),
    ("off", "pause history persistence for this session"),
    ("on", "resume history persistence for this session"),
    ("retention", "set max entries cap (e.g. /history retention 1000)"),
)

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
