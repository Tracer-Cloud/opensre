#!/usr/bin/env python3
"""
Demo runner for the incident resolution agent.

This script provides the Rich console output for the demo.
Run with: python tests/run_demo.py

Note: Rendering is done here, not in the core runner (run_investigation is pure).
Uses LangGraph streaming to show intermediate steps.
"""

# Add project root to path FIRST
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Initialize runtime before any other imports
from config import init_runtime  # noqa: E402

init_runtime()

import json  # noqa: E402

from langsmith import traceable  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402

from src.agent.domain.state import make_initial_state  # noqa: E402
from src.agent.graph import build_graph  # noqa: E402
from src.agent.render_output.render import (  # noqa: E402
    render_investigation_start,
    render_root_cause_complete,
)
from src.ingest import parse_grafana_payload  # noqa: E402

console = Console()

# Path to fixture
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "grafana_alert.json"

# Raw alert text shown in the demo (exact formatting preserved)
RAW_ALERT_TEXT = """[ALERT] events_fact freshness SLA breached
Env: prod
Detected: 02:13 UTC

No new rows for 2h 0m (SLA 30m)
Last warehouse update: 00:13 UTC

Upstream pipeline run pending investigation
"""

# Map plan sources to human-readable names
SOURCE_NAMES = {
    "tracer": "Tracer Pipeline Status",
    "storage": "S3 Storage Check",
    "batch": "AWS Batch Jobs",
}


def load_sample_request():
    """Load the sample alert from test fixtures and parse into InvestigationRequest."""
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    return parse_grafana_payload(data)


def render_plan(plan_sources: list[str]):
    """Render the investigation plan (hypotheses to check)."""
    console.print("\n[bold magenta]─── Investigation Plan ───[/]")
    console.print("[bold]Evidence sources to check:[/]\n")
    for i, source in enumerate(plan_sources, 1):
        name = SOURCE_NAMES.get(source, source)
        console.print(f"  [cyan]H{i}[/] [bold]{name}[/]")
    console.print()


def render_evidence(evidence: dict):
    """Render collected evidence."""
    console.print("\n[bold yellow]─── Evidence Collection ───[/]")

    # S3 evidence
    if "s3" in evidence:
        s3 = evidence["s3"]
        console.print("\n[bold cyan]→ S3 Storage Check[/]")
        if s3.get("error"):
            console.print(f"  [red]Error: {s3['error']}[/]")
        else:
            marker = "[green]✓ Found[/]" if s3.get("marker_exists") else "[red]✗ Missing[/]"
            console.print(f"  [dim]_SUCCESS marker:[/] {marker}")
            console.print(f"  [dim]Files found:[/] {s3.get('file_count', 0)}")
            if s3.get("files"):
                for f in s3["files"][:3]:
                    console.print(f"    [dim]- {f}[/]")

    # Pipeline run evidence
    if "pipeline_run" in evidence:
        run = evidence["pipeline_run"]
        console.print("\n[bold cyan]→ Tracer Pipeline Status[/]")
        if not run.get("found"):
            console.print("  [yellow]No recent pipeline runs found[/]")
        else:
            status = run.get("status", "Unknown")
            status_color = "red bold" if status.lower() == "failed" else "green"
            console.print(f"  [dim]Pipeline:[/] {run.get('pipeline_name', 'Unknown')}")
            console.print(f"  [dim]Run:[/] {run.get('run_name', 'Unknown')}")
            console.print(f"  [dim]Status:[/] [{status_color}]{status}[/]")
            console.print(f"  [dim]Duration:[/] {run.get('run_time_minutes', 0)} min")
            console.print(f"  [dim]Cost:[/] [yellow]${run.get('run_cost_usd', 0):.2f}[/]")
            console.print(f"  [dim]User:[/] {run.get('user_email', 'Unknown')}")

    # Batch jobs evidence
    if "batch_jobs" in evidence:
        batch = evidence["batch_jobs"]
        console.print("\n[bold cyan]→ AWS Batch Jobs[/]")
        if not batch.get("found"):
            console.print("  [yellow]No AWS Batch jobs found[/]")
        else:
            console.print(f"  [dim]Total jobs:[/] {batch.get('total_jobs', 0)}")
            console.print(f"  [dim]Succeeded:[/] [green]{batch.get('succeeded_jobs', 0)}[/]")
            failed = batch.get('failed_jobs', 0)
            if failed > 0:
                console.print(f"  [dim]Failed:[/] [red bold]{failed}[/]")
                if batch.get("failure_reason"):
                    console.print(f"  [red bold]Failure reason:[/] [red]{batch['failure_reason']}[/]")


def render_analysis(root_cause: str, confidence: float):
    """Render the root cause analysis."""
    console.print("\n[bold green]─── Root Cause Analysis ───[/]")

    # Parse bullet points from root_cause
    bullets = [line.strip().lstrip("*- ") for line in root_cause.split("\n") if line.strip()]
    render_root_cause_complete(bullets, confidence)


@traceable
def run_demo():
    """Run the LangGraph incident resolution demo with Rich console output."""
    console.print("\n")

    # Load alert from test fixture
    request = load_sample_request()

    # Show the raw incoming Slack alert (what triggers the agent)
    console.print(Panel(
        RAW_ALERT_TEXT,
        title="Incoming Grafana Alert (Slack Channel)",
        border_style="red"
    ))
    console.print("[dim]Agent triggered automatically...[/dim]\n")

    # Render investigation start
    render_investigation_start(
        request.alert_name,
        request.affected_table,
        request.severity,
    )

    # Build graph and initial state
    graph = build_graph()
    initial_state = make_initial_state(
        alert_name=request.alert_name,
        affected_table=request.affected_table,
        severity=request.severity,
    )

    # Stream the graph execution to show intermediate steps
    # Accumulate state as we go
    accumulated_state = dict(initial_state)

    for event in graph.stream(initial_state, stream_mode="updates"):
        for node_name, node_output in event.items():
            # Merge node output into accumulated state
            accumulated_state.update(node_output)

            # Render based on node
            if node_name == "plan":
                render_plan(node_output.get("plan_sources", []))
            elif node_name == "gather_evidence":
                render_evidence(node_output.get("evidence", {}))
            elif node_name == "analyze":
                render_analysis(
                    node_output.get("root_cause", ""),
                    node_output.get("confidence", 0.0),
                )

    final_state = accumulated_state

    # Show RCA Report (combined output)
    console.print("\n")
    console.print(Panel(
        final_state["slack_message"],
        title="RCA Report",
        border_style="green"
    ))

    return final_state


if __name__ == "__main__":
    run_demo()

