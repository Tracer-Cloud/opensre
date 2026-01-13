"""
Main entry point for the incident resolution demo.

LangGraph state machine:
    START → check_s3 → check_nextflow → determine_root_cause → output → END

Two external context calls, deterministic decision, no network except optional LLM.
"""

import json
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.graph import run_investigation

load_dotenv()
console = Console()


def load_sample_alert() -> GrafanaAlertPayload:
    """Load the sample Grafana alert from fixtures."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "grafana_alert.json"
    with open(fixture_path) as f:
        data = json.load(f)
    return GrafanaAlertPayload(**data)


def main():
    """Run the LangGraph incident resolution demo."""
    console.print("\n")
    console.print(Panel(
        "[bold blue]Agentic AI for Data Pipeline Incident Resolution[/bold blue]\n\n"
        "LangGraph state machine: START → check_s3 → check_nextflow → root_cause → output → END\n"
        "• Two external calls (S3 + Nextflow)\n"
        "• Deterministic root cause decision\n"
        "• Outputs: Slack message + problem.md",
        title="🚀 LANGGRAPH DEMO",
        border_style="blue"
    ))

    # Load alert
    console.print("\n[bold]Loading Grafana Alert...[/bold]")
    grafana_payload = load_sample_alert()
    alert = normalize_grafana_alert(grafana_payload)
    console.print(f"   Alert: {alert.alert_name} | Severity: {alert.severity} | Table: {alert.affected_table}")

    # Run the graph
    final_state = run_investigation(
        alert_name=alert.alert_name,
        affected_table=alert.affected_table or "events_fact",
        severity=alert.severity,
    )

    # Save outputs
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)

    # problem.md
    md_path = output_dir / "problem.md"
    md_path.write_text(final_state["problem_md"])
    console.print(f"\n[green]✓[/green] Saved: {md_path}")

    # slack_message.txt
    slack_path = output_dir / "slack_message.txt"
    slack_path.write_text(final_state["slack_message"])
    console.print(f"[green]✓[/green] Saved: {slack_path}")

    # Summary
    console.print(Panel(
        f"[bold]Root Cause:[/bold] {final_state['root_cause']}\n\n"
        f"[bold]Confidence:[/bold] {final_state['confidence']:.0%}",
        title="✅ DONE",
        border_style="green"
    ))


if __name__ == "__main__":
    main()

