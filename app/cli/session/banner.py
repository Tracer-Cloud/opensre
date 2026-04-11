"""Session banner and status rendering."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.cli.session.state import SessionState
from app.version import get_version

_console = Console(highlight=False)


def render_banner(state: SessionState) -> None:
    trust = "on" if state.trust_mode else "off"
    status = "idle" if not state.active_run else "running"
    subtitle = f"v{get_version()}   model: {state.model_label}"

    title = Text.assemble(("OpenSRE Session", "bold cyan"))
    body = Text.assemble(
        ("Investigation terminal for alert triage and RCA\n", "white"),
        ("/help", "bold cyan"),
        (" commands   ", "dim"),
        ("/quit", "bold cyan"),
        (" exit   ", "dim"),
        ("trust:", "dim"),
        (trust, "bold green" if trust == "on" else "bold yellow"),
        ("   status:", "dim"),
        (status, "bold white"),
    )

    _console.print()
    _console.print(
        Panel(
            body,
            title=title,
            subtitle=subtitle,
            subtitle_align="right",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    _console.print()


def render_status(state: SessionState) -> None:
    trust = "on" if state.trust_mode else "off"
    status = "running" if state.active_run else "idle"
    last_alert = "none"
    if state.last_alert:
        last_alert = str(state.last_alert.get("alert_name", "alert"))
    turns = len(state.conversation)

    table = Table(box=box.SIMPLE_HEAD, show_header=False, pad_edge=False)
    table.add_column("k", style="dim", no_wrap=True)
    table.add_column("v", style="white")
    table.add_row("status", status)
    table.add_row("trust", trust)
    table.add_row("model", state.model_label)
    table.add_row("last alert", last_alert)
    table.add_row("turns", str(turns))
    if state.last_duration_s is not None:
        table.add_row("last run", f"{state.last_duration_s:.1f}s")
    _console.print(table)


def render_run_summary(state: SessionState) -> None:
    if not state.last_result:
        return

    last_alert = "none"
    if state.last_alert:
        last_alert = str(state.last_alert.get("alert_name", "alert"))

    root_cause = str(state.last_result.get("root_cause", "")).strip()
    report = str(state.last_result.get("report", "")).strip()
    if len(report) > 240:
        report = report[:237] + "..."

    summary_table = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    summary_table.add_column("k", style="dim", no_wrap=True)
    summary_table.add_column("v", style="white")
    summary_table.add_row("alert", last_alert)
    summary_table.add_row(
        "duration", f"{state.last_duration_s:.1f}s" if state.last_duration_s else "n/a"
    )
    summary_table.add_row("noise", str(bool(state.last_result.get("is_noise", False))).lower())
    summary_table.add_row("root cause", root_cause or "n/a")
    summary_table.add_row("report", report or "n/a")

    _console.print(
        Panel(
            summary_table,
            title=Text.assemble(("Last Investigation", "bold green")),
            border_style="green",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )
