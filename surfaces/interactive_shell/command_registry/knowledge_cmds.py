"""Slash ``/knowledge`` — workspace knowledge and memory health, like ``opensre knowledge``."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from config.constants import OPENSRE_KNOWLEDGE_DISABLED_ENV
from config.constants.organization import organization_id
from core.domain.knowledge import (
    knowledge_dir,
    knowledge_enabled,
    list_workspace_entries,
    load_workspace_entry,
)
from core.domain.memory import audit_memories, live_memories, memory_dir, slugify
from core.domain.memory.usage import load_usage
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import BOLD_BRAND, DIM, ERROR, HIGHLIGHT

_DESCRIPTION_WIDTH = 72


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _DESCRIPTION_WIDTH else text[: _DESCRIPTION_WIDTH - 1] + "…"


def _show_list(console: Console) -> bool:
    entries = list_workspace_entries()
    if not entries:
        console.print(
            f"[{DIM}]no workspace knowledge yet. Connect Slack, then run[/] "
            f"[{HIGHLIGHT}]/knowledge scan[/]"
        )
    for entry in entries:
        console.print(
            f"[{BOLD_BRAND}]{escape(entry.slug)}[/] [{DIM}]({entry.memory_type}, "
            f"updated {entry.updated_at[:10]})[/] {escape(_clip(entry.description))}"
        )
    console.print(
        f"[{DIM}]{escape(str(knowledge_dir()))} · your own memory:[/] [{HIGHLIGHT}]/memory[/]"
    )
    return True


def _show_one(console: Console, args: list[str]) -> bool:
    if not args:
        console.print(f"[{ERROR}]usage:[/] /knowledge show <name>")
        return True
    entry = load_workspace_entry(slugify(args[0]))
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
    audit = audit_memories(live_memories(), usage=load_usage(memory_dir()))
    findings = ", ".join(f"{rule} {count}" for rule, count in audit.summary()["findings"].items())
    console.print(
        f"[{BOLD_BRAND}]{audit.total} memories, health {audit.score}/100[/]"
        + (f" [{DIM}]— {escape(findings)}[/]" if findings else "")
    )
    for group in audit.duplicate_groups:
        console.print(f"  [{DIM}]duplicates:[/] {escape(' = '.join(group))}")
    for finding in audit.findings:
        if finding.rule != "duplicate":
            console.print(
                f"  [{DIM}]{finding.rule}:[/] {escape(finding.slug)} [{DIM}]{escape(finding.detail)}[/]"
            )
    return True


def _scan(console: Console, args: list[str]) -> bool:
    from integrations.knowledge_scans import KnowledgeScanService

    service = KnowledgeScanService(organization=organization_id())
    wanted = args or list(service.services)
    unknown = [name for name in wanted if name not in service.services]
    if unknown:
        console.print(
            f"[{ERROR}]cannot scan[/] {escape(', '.join(unknown))} "
            f"[{DIM}](scannable: {', '.join(service.services)})[/]"
        )
        return True
    for name in wanted:
        service.request_rescan(name)
    outcomes = service.run_due()
    if not outcomes:
        console.print(f"[{DIM}]nothing to scan: no scannable integration is connected.[/]")
    for outcome in outcomes:
        if outcome.ok:
            console.print(
                f"[{HIGHLIGHT}]{outcome.service}[/] wrote {escape(', '.join(outcome.written))}"
            )
        else:
            console.print(f"[{ERROR}]{outcome.service}:[/] {escape(outcome.error)}")
    return True


def _cmd_knowledge(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not knowledge_enabled():
        console.print(f"[{DIM}]workspace knowledge is off ({OPENSRE_KNOWLEDGE_DISABLED_ENV}=1).[/]")
        return True
    sub = args[0].lower() if args else "list"
    if sub == "list":
        return _show_list(console)
    if sub == "show":
        return _show_one(console, args[1:])
    if sub == "audit":
        return _audit(console)
    if sub == "scan":
        return _scan(console, args[1:])
    console.print(f"[{ERROR}]usage:[/] /knowledge [list|show <name>|audit|scan [service]]")
    return True


_KNOWLEDGE_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list workspace knowledge"),
    ("show", "print one workspace entry in full (/knowledge show <name>)"),
    ("audit", "check your memory: duplicates, one-off notes, missing evidence"),
    ("scan", "scan connected integrations now (/knowledge scan [service])"),
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
        notes=(
            "Workspace knowledge is shared by everyone in the organization and "
            "written by scans of connected integrations; /memory is your own.",
            f"Disable it with {OPENSRE_KNOWLEDGE_DISABLED_ENV}=1.",
        ),
        first_arg_completions=_KNOWLEDGE_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
