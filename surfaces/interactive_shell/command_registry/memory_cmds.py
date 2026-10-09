"""Slash commands: long-term memory management (/memory)."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from config.constants import OPENSRE_MEMORY_DISABLED_ENV
from core.domain.memory import (
    delete_memory,
    list_memories,
    load_memory,
    memory_dir,
    memory_enabled,
    memory_path,
    slugify,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    print_repl_table,
    repl_table,
)


def _disabled_notice(console: Console) -> bool:
    console.print(f"[{DIM}]memory is disabled ({OPENSRE_MEMORY_DISABLED_ENV}=1).[/]")
    return True


def _show_list(console: Console) -> bool:
    from core.domain.memory import ensure_memory_store

    ensure_memory_store()
    records = list_memories()
    if not records:
        console.print(
            f"[{DIM}]no memories stored yet. Share durable facts in normal chat "
            "(e.g. our prod cluster is eks-prod-1) — useful ones are saved automatically.[/]"
        )
        return True

    table = repl_table(title="Long-term memory\n", title_style=BOLD_BRAND)
    table.add_column("name", style="bold")
    table.add_column("type", style=DIM)
    table.add_column("description", overflow="fold")
    table.add_column("updated", style=DIM)
    for record in records:
        table.add_row(
            escape(record.slug),
            record.memory_type,
            escape(record.description),
            record.updated_at[:10],
        )
    print_repl_table(console, table)
    console.print(
        f"[{DIM}]stored unencrypted in {memory_dir()} — edit or delete the files "
        f"directly, or use[/] [{HIGHLIGHT}]/memory forget <name>[/][{DIM}]. "
        f"Disable with {OPENSRE_MEMORY_DISABLED_ENV}=1.[/]"
    )
    return True


def _show_one(console: Console, args: list[str]) -> bool:
    if not args:
        console.print(f"[{ERROR}]usage:[/] /memory show <name>")
        return True
    name = args[0]
    record = load_memory(slugify(name))
    if record is None:
        console.print(f"[{ERROR}]no memory named[/] {escape(name)}")
        return True
    console.print(f"[{BOLD_BRAND}]{escape(record.slug)}[/] [{DIM}]({record.memory_type})[/]")
    console.print(f"[{DIM}]{escape(record.description)}[/]")
    console.print(f"[{DIM}]file: {memory_path(record.slug)}[/]\n")
    console.print(escape(record.body))
    return True


def _forget(console: Console, args: list[str]) -> bool:
    if not args:
        console.print(f"[{ERROR}]usage:[/] /memory forget <name>")
        return True
    name = args[0]
    if delete_memory(slugify(name)):
        console.print(f"[{HIGHLIGHT}]forgot[/] {escape(name)}")
    else:
        console.print(f"[{ERROR}]no memory named[/] {escape(name)}")
    return True


def _show_path(console: Console) -> bool:
    from core.domain.memory import ensure_memory_store

    ensure_memory_store()
    console.print(str(memory_dir()))
    return True


def _cmd_memory(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not memory_enabled():
        return _disabled_notice(console)
    if not args:
        return _show_list(console)

    sub = args[0].lower()
    if sub == "show":
        return _show_one(console, args[1:])
    if sub == "forget":
        return _forget(console, args[1:])
    if sub == "path":
        return _show_path(console)

    console.print(f"[{ERROR}]usage:[/] /memory [show <name>|forget <name>|path]")
    return True


_MEMORY_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("show", "print one memory in full (/memory show <name>)"),
    ("forget", "delete one memory (/memory forget <name>)"),
    ("path", "print the memory directory path"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/memory",
        "List and manage the agent's long-term memory.",
        _cmd_memory,
        usage=(
            "/memory",
            "/memory show <name>",
            "/memory forget <name>",
            "/memory path",
        ),
        notes=(
            "Memories are plain markdown files under ~/.opensre/memory; "
            "edit or delete them directly at any time.",
        ),
        first_arg_completions=_MEMORY_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
