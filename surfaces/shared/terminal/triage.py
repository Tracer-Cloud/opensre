"""Literal, responsive triage presentation shared by CLI and shell."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from infrastructure.terminal.theme import DIM, SECONDARY
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def print_investigations(console: Console, records: list[dict[str, Any]]) -> None:
    """Show independent alert lifecycle and investigation outcome with full IDs."""
    if not records:
        console.print(
            Text(
                "No investigations yet. Connect SigNoz alerts or try the payment-error demo.",
                style=SECONDARY,
            )
        )
        return
    rows = tuple(
        RecordRow(
            cells=(Text(r["source_id"]), Text(r["lifecycle"]), Text(r["state"])),
            metadata=(Text(f"ID: {r['id']}", style=DIM),),
            details=(Text(r["failure"], style=SECONDARY),) if r.get("failure") else (),
        )
        for r in records
    )
    console.print(
        RecordTable(
            "Investigations",
            (RecordColumn("Source"), RecordColumn("Alert"), RecordColumn("Investigation")),
            rows,
            caption="Read a complete report with /triage show <id>.",
        )
    )


def print_report(console: Console, record: dict[str, Any]) -> None:
    """Render the current lifecycle alongside append-only report revisions."""
    console.print(
        Text(f"Investigation {record['id']} · alert {record['lifecycle']}", style=SECONDARY)
    )
    for job in record["investigations"]:
        console.print(Text(f"{job['state']} · {job['id']}", style=DIM))
        if job.get("question"):
            console.print(Text(f"Follow-up: {job['question']}"))
        report = job.get("report")
        if report:
            for key, label in (
                ("observed", "Observed"),
                ("likely_cause", "Likely cause"),
                ("unknowns", "Unknowns"),
                ("next_check", "Next check"),
            ):
                console.print(Text(f"{label}: {report.get(key, 'Unknown')}"))
            console.print(
                Text(
                    f"Elapsed: {report.get('elapsed_seconds', 'unknown')}s · Tokens: {report.get('tokens') or 'unknown'} · Cost: {report.get('cost_usd') if report.get('cost_usd') is not None else 'unknown'}",
                    style=DIM,
                )
            )
        for evidence in job["evidence"]:
            console.print(Text(f"Evidence #{evidence['id']}: {evidence['query']}", style=SECONDARY))
            if evidence["result"] is not None:
                console.print(Text(str(evidence["result"])))
