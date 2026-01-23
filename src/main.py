"""
Main entry point for the incident resolution demo.

LangGraph state machine:
    START -> check_s3 -> check_tracer -> determine_root_cause -> output -> END

Uses Tracer API for pipeline data, LLM for analysis.
"""

# Load environment variables FIRST, before any other imports
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path, override=True)

# Verify API key is set
if not os.getenv("ANTHROPIC_API_KEY"):
    import sys
    print("ERROR: ANTHROPIC_API_KEY not found in environment or .env file", file=sys.stderr)
    print(f"Please create a .env file at {env_path} with:", file=sys.stderr)
    print("ANTHROPIC_API_KEY=your_api_key_here", file=sys.stderr)
    sys.exit(1)

import json
from rich.console import Console
from rich.panel import Panel

from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.graph import run_investigation
console = Console()


def load_sample_alert() -> GrafanaAlertPayload:
    """Load the sample Grafana alert from fixtures."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "grafana_alert.json"
    with open(fixture_path) as f:
        data = json.load(f)
    return GrafanaAlertPayload(**data)
from langsmith import traceable

@traceable
def main():
    """Run the LangGraph incident resolution demo."""
    console.print("\n")

    # Load alert
    grafana_payload = load_sample_alert()
    alert = normalize_grafana_alert(grafana_payload)

    # Show the raw incoming Slack alert (what triggers the agent)
    raw_alert = """[ALERT] events_fact freshness SLA breached
Env: prod
Detected: 02:13 UTC

No new rows for 2h 0m (SLA 30m)
Last warehouse update: 00:13 UTC

Upstream pipeline run pending investigation
"""
    console.print(Panel(raw_alert, title="Incoming Grafana Alert (Slack Channel)", border_style="red"))
    console.print("[dim]Agent triggered automatically...[/dim]\n")

    # Run the graph
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

    # Save outputs
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)

    # problem.md
    md_path = output_dir / "problem.md"
    md_path.write_text(final_state["problem_md"])
    console.print(f"[green][OK][/green] Saved: {md_path}")

    # slack_message.txt
    slack_path = output_dir / "slack_message.txt"
    slack_path.write_text(final_state["slack_message"])
    console.print(f"[green][OK][/green] Saved: {slack_path}")


if __name__ == "__main__":
    main()

