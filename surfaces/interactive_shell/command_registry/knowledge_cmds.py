"""Slash ``/knowledge`` — workspace knowledge and memory health, like ``opensre knowledge``.

Gateways reach this command too, so everything but the member's own audit
goes through the same membership rule as the prompt and ``memory_recall``,
and a scan request is handed to the gateway's running scan loop rather than
run inside the chat turn (:func:`integrations.knowledge_scans.scan_on_request`).
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from config.constants import OPENSRE_KNOWLEDGE_DISABLED_ENV
from core.domain.knowledge import (
    knowledge_available_here,
    knowledge_dir,
    knowledge_enabled,
    readable_workspace_entries,
    readable_workspace_entry,
)
from core.domain.memory import audit_memories, memory_dir, read_member_store, slugify
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import BOLD_BRAND, DIM, ERROR, HIGHLIGHT
from surfaces.shared.knowledge_text import audit_lines, entry_line, scan_lines


def _show_list(console: Console) -> bool:
    entries = readable_workspace_entries()
    if not entries:
        console.print(
            f"[{DIM}]no workspace knowledge yet. Connect Slack, then run[/] "
            f"[{HIGHLIGHT}]/knowledge scan[/]"
        )
    for entry in entries:
        console.print(escape(entry_line(entry)))
    console.print(
        f"[{DIM}]{escape(str(knowledge_dir()))} · your own memory:[/] [{HIGHLIGHT}]/memory[/]"
    )
    return True


def _show_one(console: Console, args: list[str]) -> bool:
    if not args:
        console.print(f"[{ERROR}]usage:[/] /knowledge show <name>")
        return True
    entry = readable_workspace_entry(slugify(args[0]))
    if entry is None:
        console.print(f"[{ERROR}]no workspace entry named[/] {escape(args[0])}")
        return True
    console.print(f"[{BOLD_BRAND}]{escape(entry.slug)}[/] [{DIM}]({entry.memory_type})[/]")
    console.print(f"[{DIM}]{escape(entry.description)}[/]")
    if entry.evidence:
        console.print(f"[{DIM}]evidence: {escape(entry.evidence)}[/]\n")
    console.print(escape(entry.body))
    return True


def _audit(console: Console) -> bool:
    store = read_member_store("you", memory_dir())
    audit = audit_memories(store.records, usage=store.usage).to_dict()
    for line in audit_lines("you", audit):
        console.print(escape(line))
    return True


def _scan(console: Console, args: list[str]) -> bool:
    from integrations.knowledge_scans import SCANNABLE_SERVICES, scan_on_request

    outcomes, unknown = scan_on_request(args, force=False)
    for line in scan_lines(outcomes, unknown, SCANNABLE_SERVICES):
        console.print(escape(line))
    return True


def _cmd_knowledge(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not knowledge_enabled():
        console.print(f"[{DIM}]workspace knowledge is off ({OPENSRE_KNOWLEDGE_DISABLED_ENV}=1).[/]")
        return True
    sub = args[0].lower() if args else "list"
    if sub == "audit":
        return _audit(console)
    if sub not in {"list", "show", "scan"}:
        console.print(f"[{ERROR}]usage:[/] /knowledge [list|show <name>|audit|scan [service]]")
        return True
    if not knowledge_available_here():
        console.print(f"[{DIM}]workspace knowledge is for members of this organization.[/]")
        return True
    if sub == "show":
        return _show_one(console, args[1:])
    if sub == "scan":
        return _scan(console, args[1:])
    return _show_list(console)


_SHARED_NOTE = (
    "Workspace knowledge is shared by everyone in the organization and "
    "written by scans of connected integrations; /memory is your own."
)

_KNOWLEDGE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list workspace knowledge"),
    ("show", "print one workspace entry in full (/knowledge show <name>)"),
    ("audit", "check your memory: duplicates, one-off notes, missing evidence"),
    ("scan", "scan a connected integration now (/knowledge scan <service>)"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/knowledge",
        "Show workspace knowledge and check memory health.",
        _cmd_knowledge,
        usage=(
            "/knowledge",
            "/knowledge show <name>",
            "/knowledge audit",
            "/knowledge scan [service]",
        ),
        notes=(_SHARED_NOTE, f"Disable it with {OPENSRE_KNOWLEDGE_DISABLED_ENV}=1."),
        first_arg_completions=_KNOWLEDGE_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
