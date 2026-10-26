"""``opensre knowledge`` — see and maintain what the agent knows.

Two tiers: your own long-term memory and the organization's workspace
knowledge (written by background scans of connected integrations). Workspace
knowledge is shown only to members of the organization, judged on the turn
that started this process when an agent runs the command.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import click

from core.domain.knowledge import (
    knowledge_available_here,
    knowledge_dir,
    knowledge_enabled,
    readable_workspace_entries,
    readable_workspace_entry,
)
from core.domain.memory import (
    MemoryRecord,
    audit_memories,
    iter_member_stores,
    live_memories,
    load_live_memory,
    memory_dir,
    read_member_store,
    slugify,
)
from infrastructure.process.exit_codes import ERROR, SUCCESS
from infrastructure.process.runtime_flags import is_json_output
from surfaces.shared.knowledge_text import audit_lines, entry_line, scan_lines


@click.group(name="knowledge", invoke_without_command=True)
@click.pass_context
def knowledge_command(ctx: click.Context) -> None:
    """See what the agent knows: your memory and workspace knowledge."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_command)


def _rows(records: Sequence[MemoryRecord]) -> list[dict[str, str]]:
    return [
        {
            "name": record.slug,
            "type": record.memory_type.value,
            "updated": record.updated_at[:10],
            "description": record.description,
        }
        for record in records
    ]


def _echo_section(title: str, location: str, records: Sequence[MemoryRecord]) -> None:
    click.echo(f"{title} ({len(records)}) — {location}")
    if not records:
        click.echo("  (nothing yet)")
    for record in records:
        click.echo(f"  {entry_line(record)}")
    click.echo("")


@knowledge_command.command(name="list")
def list_command() -> None:
    """List your memories and the organization's workspace knowledge."""
    personal = live_memories()
    workspace = readable_workspace_entries()
    if is_json_output():
        click.echo(json.dumps({"personal": _rows(personal), "workspace": _rows(workspace)}))
        raise SystemExit(SUCCESS)
    _echo_section("Your memory", str(memory_dir()), personal)
    _echo_section("Workspace knowledge", str(knowledge_dir()), workspace)
    click.echo("Show one: opensre knowledge show <name> · refresh: opensre knowledge scan")
    raise SystemExit(SUCCESS)


@knowledge_command.command(name="show")
@click.argument("name")
def show_command(name: str) -> None:
    """Print one memory or workspace entry in full (your memory first)."""
    slug = slugify(name)
    record = load_live_memory(slug)
    scope = "personal"
    if record is None:
        record, scope = readable_workspace_entry(slug), "workspace"
    if record is None:
        click.echo(f"Nothing named {name!r}.", err=True)
        raise SystemExit(ERROR)
    if is_json_output():
        click.echo(json.dumps({"scope": scope, **_rows([record])[0], "content": record.body}))
        raise SystemExit(SUCCESS)
    provenance = f"source {record.source}" if record.source else "no provenance"
    if record.evidence:
        provenance += f": {record.evidence}"
    click.echo(
        f"{record.slug} ({record.memory_type.value}, {scope}, updated {record.updated_at[:10]})"
    )
    click.echo(record.description)
    click.echo(provenance)
    click.echo("")
    click.echo(record.body)
    raise SystemExit(SUCCESS)


@knowledge_command.command(name="scan")
@click.option(
    "--service",
    "services",
    multiple=True,
    help="Scan only this integration (repeatable). Default: every connected one that is due.",
)
@click.option("--force", is_flag=True, help="Scan even when the knowledge is still fresh.")
def scan_command(services: tuple[str, ...], force: bool) -> None:
    """Scan connected integrations into workspace knowledge now."""
    if not knowledge_enabled() or not knowledge_available_here():
        click.echo("Workspace knowledge is not available here.", err=True)
        raise SystemExit(ERROR)
    from integrations.knowledge_scans import SCANNABLE_SERVICES, scan_on_request

    outcomes, unknown = scan_on_request(services, force=force)
    if is_json_output():
        click.echo(
            json.dumps(
                {
                    "queued": outcomes is None,
                    "unknown": unknown,
                    "scans": [
                        {
                            "service": o.service,
                            "ok": o.ok,
                            "written": list(o.written),
                            "error": o.error,
                        }
                        for o in outcomes or []
                    ],
                }
            )
        )
    else:
        for line in scan_lines(outcomes, unknown, SCANNABLE_SERVICES):
            click.echo(line)
    failed = bool(unknown) or any(not outcome.ok for outcome in outcomes or [])
    raise SystemExit(ERROR if failed else SUCCESS)


@knowledge_command.command(name="audit")
@click.option(
    "--all-users",
    is_flag=True,
    help="Audit every member's memory in this organization (on a gateway host).",
)
def audit_command(all_users: bool) -> None:
    """Report memory quality: duplicates, transient notes, missing provenance, staleness."""
    stores = list(iter_member_stores()) if all_users else [read_member_store("you", memory_dir())]
    audits = [
        {"actor": store.actor, **audit_memories(store.records, usage=store.usage).to_dict()}
        for store in stores
    ]
    if is_json_output():
        click.echo(json.dumps({"members": audits}))
        raise SystemExit(SUCCESS)
    if not audits:
        click.echo("No memory stores found.")
    for audit in audits:
        for line in audit_lines(audit["actor"], audit):
            click.echo(line)
    raise SystemExit(SUCCESS)


__all__ = ["knowledge_command"]
