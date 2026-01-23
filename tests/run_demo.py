#!/usr/bin/env python3
"""
Demo runner for the incident resolution agent.

This script provides the Rich console output for the demo.
Run with: python tests/run_demo.py

Note: Rendering is done here, not in the core runner (run_investigation is pure).
"""

# Add project root to path FIRST
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Initialize runtime before any other imports
from config import init_runtime
init_runtime()

import json
from rich.console import Console
from rich.panel import Panel
from langsmith import traceable

from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.graph import run_investigation
from src.agent.render_output.render import render_investigation_start

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


def load_sample_alert() -> GrafanaAlertPayload:
    """Load the sample Grafana alert from test fixtures."""
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    return GrafanaAlertPayload(**data)


@traceable
def run_demo():
    """Run the LangGraph incident resolution demo with Rich console output."""
    console.print("\n")

    # Load alert from test fixture
    grafana_payload = load_sample_alert()
    alert = normalize_grafana_alert(grafana_payload)

    # Show the raw incoming Slack alert (what triggers the agent)
    console.print(Panel(
        RAW_ALERT_TEXT,
        title="Incoming Grafana Alert (Slack Channel)",
        border_style="red"
    ))
    console.print("[dim]Agent triggered automatically...[/dim]\n")

    # Render investigation start (demo-only rendering)
    render_investigation_start(
        alert.alert_name,
        alert.affected_table or "events_fact",
        alert.severity,
    )

    # Run the graph (pure: inputs in, state out)
    final_state = run_investigation(
        alert_name=alert.alert_name,
        affected_table=alert.affected_table or "events_fact",
        severity=alert.severity,
    )

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

