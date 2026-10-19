"""``opensre knowledge`` — see and maintain what the agent knows.

Two tiers: your own long-term memory and the organization's workspace
knowledge (written by background scans of connected integrations).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import click

from config.constants.organization import organization_id
from core.domain.knowledge import (
    knowledge_dir,
    knowledge_enabled,
    list_workspace_entries,
    load_workspace_entry,
)
from core.domain.memory import (
    MemoryRecord,
    MemoryUsage,
    audit_memories,
    iter_member_stores,
    live_memories,
    load_live_memory,
    memory_dir,
    slugify,
)
from core.domain.memory.usage import load_usage
from infrastructure.process.exit_codes import ERROR, SUCCESS
from infrastructure.process.runtime_flags import is_json_output

_DESCRIPTION_WIDTH = 72


@click.group(name="knowledge", invoke_without_command=True)
@click.pass_context
def knowledge_command(ctx: click.Context) -> None:
    """See what the agent knows: your memory and workspace knowledge."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(list_command)


def _clip(text: str, width: int = _DESCRIPTION_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


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
        click.echo(
            f"  {record.slug:<40} {record.memory_type.value:<22} "
            f"{record.updated_at[:10]}  {_clip(record.description)}"
        )
    click.echo("")


@knowledge_command.command(name="list")
def list_command() -> None:
    """List your memories and the organization's workspace knowledge."""
    personal = live_memories()
    workspace = list_workspace_entries() if knowledge_enabled() else []
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
    if record is None and knowledge_enabled():
        record, scope = load_workspace_entry(slug), "workspace"
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
    if not knowledge_enabled():
        click.echo("Workspace knowledge is turned off here.", err=True)
        raise SystemExit(ERROR)
    from integrations.knowledge_scans import KnowledgeScanService

    service = KnowledgeScanService(organization=organization_id())
    wanted = set(services) or (set(service.services) if force else set())
    unknown = wanted - set(service.services)
    if unknown:
        click.echo(
            f"Unknown integration: {', '.join(sorted(unknown))}. "
            f"Scannable: {', '.join(service.services)}.",
            err=True,
        )
        raise SystemExit(ERROR)
    for name in wanted:
        service.request_rescan(name)
    outcomes = service.run_due()
    if is_json_output():
        click.echo(json.dumps({"scans": service.status_rows()}))
        raise SystemExit(SUCCESS if all(outcome.ok for outcome in outcomes) else ERROR)
    if not outcomes:
        click.echo("Nothing to scan: every connected integration is up to date (use --force).")
        raise SystemExit(SUCCESS)
    for outcome in outcomes:
        if outcome.ok:
            detail = ", ".join(f"{key} {value}" for key, value in sorted(outcome.details.items()))
            click.echo(f"{outcome.service}: wrote {', '.join(outcome.written)} ({detail})")
        else:
            click.echo(f"{outcome.service}: {outcome.error}", err=True)
    raise SystemExit(SUCCESS if all(outcome.ok for outcome in outcomes) else ERROR)


def _audit_payload(
    actor: str, records: Sequence[MemoryRecord], usage: dict[str, MemoryUsage]
) -> dict[str, Any]:
    return {"actor": actor, **audit_memories(records, usage=usage).to_dict()}


@knowledge_command.command(name="audit")
@click.option(
    "--all-users",
    is_flag=True,
    help="Audit every member's memory in this organization (on a gateway host).",
)
def audit_command(all_users: bool) -> None:
    """Report memory quality: duplicates, transient notes, missing provenance, staleness."""
    if all_users:
        audits = [
            _audit_payload(store.actor, store.records, store.usage)
            for store in iter_member_stores()
        ]
    else:
        directory = memory_dir()
        audits = [_audit_payload("you", live_memories(), load_usage(directory))]
    if is_json_output():
        click.echo(json.dumps({"members": audits}))
        raise SystemExit(SUCCESS)
    if not audits:
        click.echo("No memory stores found.")
        raise SystemExit(SUCCESS)
    for audit in audits:
        findings = ", ".join(f"{rule} {count}" for rule, count in audit["findings"].items())
        click.echo(
            f"{audit['actor']}: {audit['total']} memories, health {audit['score']}/100, "
            f"{audit['with_provenance']} with provenance" + (f" — {findings}" if findings else "")
        )
        for group in audit["duplicate_group_slugs"]:
            click.echo(f"  duplicates: {' = '.join(group)}")
        for issue in audit["issues"]:
            if issue["rule"] != "duplicate":
                click.echo(f"  {issue['rule']:<16} {issue['name']}: {issue['detail']}")
    raise SystemExit(SUCCESS)


__all__ = ["knowledge_command"]
