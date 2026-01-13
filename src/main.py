"""
Main entry point for the incident resolution demo.

This demonstrates the full flow:
1. Receive a Grafana alert
2. Normalize the alert
3. Run the investigation agent
4. Generate reports (JSON, Slack, problem.md)
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.investigation import InvestigationAgent
from src.agent.report_generator import (
    generate_report,
    generate_slack_message,
    generate_problem_md,
)


# Load environment variables
load_dotenv()

console = Console()


def load_sample_alert() -> GrafanaAlertPayload:
    """Load the sample Grafana alert from fixtures."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "grafana_alert.json"
    with open(fixture_path) as f:
        data = json.load(f)
    return GrafanaAlertPayload(**data)


def main():
    """Run the incident resolution demo."""
    console.print("\n")
    console.print(Panel(
        "[bold blue]Agentic AI for Data Pipeline Incident Resolution[/bold blue]\n\n"
        "This demo shows an AI agent investigating a production incident:\n"
        "• Grafana alert → Agent investigates → Root cause + Fix recommendation",
        title="🚀 DEMO START",
        border_style="blue"
    ))
    
    # Check for OpenAI API key
    if not os.getenv("OPENAI_API_KEY"):
        console.print("\n[red]❌ OPENAI_API_KEY not set in environment![/red]")
        console.print("Please set your OpenAI API key in .env file")
        return
    
    # Step 1: Load and normalize alert
    console.print("\n[bold]Step 1: Loading Grafana Alert[/bold]")
    grafana_payload = load_sample_alert()
    alert = normalize_grafana_alert(grafana_payload)
    
    console.print(f"   ✓ Alert loaded: {alert.alert_name}")
    console.print(f"   ✓ Severity: {alert.severity}")
    console.print(f"   ✓ Affected table: {alert.affected_table}")
    
    # Step 2: Create and run the investigation agent
    console.print("\n[bold]Step 2: Starting Investigation Agent[/bold]")
    agent = InvestigationAgent(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        verbose=True
    )
    
    # Run investigation
    report = agent.investigate(alert)
    
    # Step 3: Generate outputs
    console.print("\n[bold]Step 3: Generating Reports[/bold]")
    
    # JSON report
    json_report = generate_report(report)
    output_dir = Path(__file__).parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    
    json_path = output_dir / "report.json"
    with open(json_path, "w") as f:
        json.dump(json_report, f, indent=2)
    console.print(f"   ✓ JSON report: {json_path}")
    
    # Slack message
    slack_message = generate_slack_message(report)
    slack_path = output_dir / "slack_message.json"
    with open(slack_path, "w") as f:
        json.dump(slack_message, f, indent=2)
    console.print(f"   ✓ Slack message: {slack_path}")
    
    # Problem.md
    md_path = output_dir / "problem.md"
    generate_problem_md(report, md_path)
    console.print(f"   ✓ Problem.md: {md_path}")
    
    # Display summary
    console.print("\n")
    console.print(Panel(
        f"[bold green]Investigation Complete![/bold green]\n\n"
        f"[bold]Root Cause:[/bold] {report.root_cause[:200]}...\n\n"
        f"[bold]Confidence:[/bold] {report.root_cause_confidence:.0%}\n\n"
        f"[bold]Duration:[/bold] {report.investigation_timeline.duration_seconds:.1f}s\n\n"
        f"[bold]Hypotheses Tested:[/bold] {report.investigation_timeline.hypotheses_tested}",
        title="✅ INVESTIGATION SUMMARY",
        border_style="green"
    ))
    
    # Show recommended actions
    console.print("\n[bold]Recommended Actions:[/bold]")
    for action in report.recommended_actions:
        priority_color = {"critical": "red", "high": "yellow", "medium": "blue"}.get(action.priority, "white")
        console.print(f"   [{priority_color}][{action.priority.upper()}][/{priority_color}] {action.action}")
    
    console.print("\n[dim]Reports saved to ./output/[/dim]")
    console.print("\n")


if __name__ == "__main__":
    main()

