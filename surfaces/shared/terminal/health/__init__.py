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

from infrastructure.terminal.theme import (
    BOLD_BRAND,
    BRAND,
    ERROR,
    HIGHLIGHT,
    SECONDARY,
    WARNING,
)
from surfaces.shared.terminal.components.rendering import print_repl_table


def status_badge(status: str) -> Text:
    normalized = status.strip().lower()
    if normalized in {"passed", "pass", "ok", "healthy"}:
        return Text("PASSED", style=f"bold {HIGHLIGHT}")
    if normalized in {"warn", "warning", "degraded", "outdated"}:
        return Text("WARN", style=f"bold {WARNING}")
    if normalized == "missing":
        return Text("MISSING", style=f"bold {WARNING}")
    if normalized in {"failed", "fail", "error", "unhealthy"}:
        return Text("FAILED", style=f"bold {ERROR}")
    return Text(normalized.upper() or "UNKNOWN", style="bold")


_STATUS_BUCKETS: dict[str, str] = {
    "passed": "passed",
    "pass": "passed",
    "ok": "passed",
    "healthy": "passed",
    "missing": "missing",
    "failed": "failed",
    "fail": "failed",
    "error": "failed",
    "unhealthy": "failed",
}


def _summary_counts(results: list[dict[str, str]]) -> dict[str, int]:
    counts = {"passed": 0, "missing": 0, "failed": 0, "other": 0}
    for result in results:
        status = str(result.get("status", "")).strip().lower()
        bucket = _STATUS_BUCKETS.get(status, "other")
        counts[bucket] += 1
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
    console.print(Panel.fit(f"[{BOLD_BRAND}]OpenSRE Health[/]", border_style=BRAND))

    from infrastructure.safety.guardrails.rules import get_default_rules_path, load_rules

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
        (f"{counts['passed']} passed", HIGHLIGHT),
        ("  |  ", SECONDARY),
        (f"{counts['missing']} missing", WARNING),
        ("  |  ", SECONDARY),
        (f"{counts['failed']} failed", ERROR),
    )
    if counts["other"]:
        summary.append("  |  ", style=SECONDARY)
        summary.append(f"{counts['other']} unknown")
    console.print(summary)
    console.print()

    table = Table(title="Integration Checks", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("Service", style=BOLD_BRAND, no_wrap=True)
    table.add_column("Source", style=SECONDARY, no_wrap=True)
    table.add_column("Status", no_wrap=True)
    # Fold long connect-timeout DETAIL so the table stays within the terminal
    # (and within COLUMNS-minus-prefix when relayed from a background task).
    detail_width = max(24, min(console.width, 120) - 36)
    table.add_column("Detail", overflow="fold", max_width=detail_width)

    for result in normalized_results:
        table.add_row(
            result["service"] or "-",
            result["source"] or "-",
            status_badge(result["status"]),
            result["detail"] or "-",
        )

    print_repl_table(console, table)
    console.print()

    if counts["failed"] > 0:
        console.print(
            f"[bold {ERROR}]Action:[/] Fix failed integrations, then rerun [bold]opensre health[/bold]."
        )
    elif counts["missing"] > 0:
        console.print(
            f"[bold {WARNING}]Action:[/] Configure missing integrations with "
            "[bold]opensre integrations setup <service>[/bold]."
        )
    else:
        console.print(f"[bold {HIGHLIGHT}]All configured integrations look healthy.[/]")


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
    click.echo(
        json.dumps(
            {
                "environment": environment,
                "integration_store": str(integration_store_path),
                "summary": counts,
                "results": normalized,
            },
            indent=2,
        )
    )
