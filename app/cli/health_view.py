"""Rendering helpers for the ``opensre health`` command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _status_badge(status: str) -> Text:
    normalized = status.strip().lower()
    if normalized == "passed":
        return Text("PASSED", style="bold green")
    if normalized == "missing":
        return Text("MISSING", style="bold yellow")
    if normalized == "failed":
        return Text("FAILED", style="bold red")
    return Text(normalized.upper() or "UNKNOWN", style="bold")


def _summary_counts(results: list[dict[str, str]]) -> dict[str, int]:
    counts = {"passed": 0, "missing": 0, "failed": 0, "other": 0}
    for result in results:
        status = str(result.get("status", "")).strip().lower()
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
    return counts


def render_health_report(
    *,
    console: Console,
    environment: str,
    integration_store_path: str | Path,
    results: list[dict[str, Any]],
) -> None:
    """Render a polished health report with summary and actionable hints."""
    store_path_text = str(integration_store_path)

    normalized_results: list[dict[str, str]] = [
        {
            "service": str(item.get("service", "")),
            "source": str(item.get("source", "")),
            "status": str(item.get("status", "")),
            "detail": str(item.get("detail", "")),
        }
        for item in results
    ]
    counts = _summary_counts(normalized_results)

    console.print()
    console.print(Panel.fit("[bold cyan]OpenSRE Health[/bold cyan]", border_style="cyan"))

    from app.guardrails.rules import get_default_rules_path, load_rules

    rules_path = get_default_rules_path()
    if rules_path.exists():
        rules = load_rules(rules_path)
        enabled = [r for r in rules if r.enabled]
        guardrails_status = f"{len(enabled)} rules active ({rules_path})"
    else:
        guardrails_status = "not configured"

    meta = Table.grid(padding=(0, 1))
    meta.add_row("[bold]Environment[/bold]", environment)
    meta.add_row("[bold]Integration store[/bold]", store_path_text)
    meta.add_row("[bold]Guardrails[/bold]", guardrails_status)
    console.print(meta)

    summary = Text.assemble(
        ("Summary: ", "bold"),
        (f"{counts['passed']} passed", "green"),
        ("  |  ", "dim"),
        (f"{counts['missing']} missing", "yellow"),
        ("  |  ", "dim"),
        (f"{counts['failed']} failed", "red"),
    )
    if counts["other"]:
        summary.append("  |  ", style="dim")
        summary.append(f"{counts['other']} unknown", style="magenta")
    console.print(summary)
    console.print()

    table = Table(title="Integration Checks", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("Service", style="bold cyan")
    table.add_column("Source", style="dim")
    table.add_column("Status")
    table.add_column("Detail")

    for result in normalized_results:
        table.add_row(
            result["service"] or "-",
            result["source"] or "-",
            _status_badge(result["status"]),
            result["detail"] or "-",
        )

    console.print(table)
    console.print()

    if counts["failed"] > 0:
        console.print(
            "[bold red]Action:[/bold red] Fix failed integrations, then rerun [bold]opensre health[/bold]."
        )
    elif counts["missing"] > 0:
        console.print(
            "[bold yellow]Action:[/bold yellow] Configure missing integrations with "
            "[bold]opensre integrations setup <service>[/bold]."
        )
    else:
        console.print("[bold green]All configured integrations look healthy.[/bold green]")


def render_health_json(
    *,
    environment: str,
    integration_store_path: str | Path,
    results: list[dict[str, Any]],
) -> None:
    """Render the health report as machine-readable JSON."""
    normalized = [
        {
            "service": str(item.get("service", "")),
            "source": str(item.get("source", "")),
            "status": str(item.get("status", "")),
            "detail": str(item.get("detail", "")),
        }
        for item in results
    ]
    counts = _summary_counts(normalized)
    click.echo(json.dumps({
        "environment": environment,
        "integration_store": str(integration_store_path),
        "summary": counts,
        "results": normalized,
    }, indent=2))
