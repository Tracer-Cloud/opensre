"""
Demo runner for the incident resolution agent.

Run with: python -m tests.run_demo

This demo:
1. Finds a real failed pipeline run from Tracer Web App
2. Creates an alert for that pipeline
3. Runs full investigation with deep multi-source analysis
4. Produces accurate RCA report

Rendering is handled in the ingestion layer and nodes.
Uses the same pipeline runner as the CLI.
"""
import base64
import json
import os
from datetime import UTC, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)

from config import init_runtime  # noqa: E402

init_runtime()

from langsmith import traceable  # noqa: E402
from rich.console import Console  # noqa: E402

from src.agent.context.context_building import _fetch_tracer_web_run_context  # noqa: E402
from src.agent.graph_pipeline import run_investigation_pipeline  # noqa: E402

console = Console()


def _extract_org_id_from_jwt(jwt_token: str) -> str | None:
    """Extract organization ID from JWT token."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        # Decode JWT payload (add padding if needed)
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)  # Add padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("organization")
    except Exception:
        return None


@traceable
def run_demo():
    """Run the LangGraph incident resolution demo with a real failed pipeline."""
    # Check required environment variables
    api_key = os.getenv("ANTHROPIC_API_KEY")
    jwt_token = os.getenv("JWT_TOKEN")
    org_id = os.getenv("TRACER_ORG_ID")
    # Set default for TRACER_WEB_APP_URL if not set
    if not os.getenv("TRACER_WEB_APP_URL"):
        os.environ["TRACER_WEB_APP_URL"] = os.getenv("TRACER_API_URL", "https://staging.tracer.cloud")

    # Try to extract org_id from JWT token if not set
    if not org_id and jwt_token:
        org_id = _extract_org_id_from_jwt(jwt_token)
        if org_id:
            # Set it in the environment for other code to use
            os.environ["TRACER_ORG_ID"] = org_id

    # Check for truly required variables
    if not api_key:
        console.print("[red]Error: Missing required environment variable: ANTHROPIC_API_KEY[/]")
        console.print(f"\nPlease set this in your .env file at: {env_path}")
        return None

    if not jwt_token:
        console.print("[red]Error: Missing required environment variable: JWT_TOKEN[/]")
        console.print(f"\nPlease set this in your .env file at: {env_path}")
        return None

    if not org_id:
        console.print("[red]Error: Missing TRACER_ORG_ID[/]")
        console.print("Could not extract it from JWT token. Please set it in your .env file:")
        console.print("  TRACER_ORG_ID=org_33W1pou1nUzYoYPZj3OCQ3jslB2")
        return None

    console.print("[bold cyan]🔍 Finding a real failed pipeline run...[/]")

    # Find a real failed run from Tracer Web App
    web_run = _fetch_tracer_web_run_context()

    if not web_run.get("found"):
        console.print("[yellow]⚠️  No failed runs found in Tracer Web App[/]")
        console.print(f"Checked {web_run.get('pipelines_checked', 0)} pipelines")
        return None

    # Extract pipeline details
    pipeline_name = web_run.get("pipeline_name", "unknown_pipeline")
    run_name = web_run.get("run_name", "unknown_run")
    trace_id = web_run.get("trace_id")
    status = web_run.get("status", "unknown")
    run_url = web_run.get("run_url")

    console.print(f"[green]✓ Found failed run:[/] {run_name}")
    console.print(f"  Pipeline: {pipeline_name}")
    console.print(f"  Status: {status}")
    if trace_id:
        console.print(f"  Trace ID: {trace_id}")
    if run_url:
        console.print(f"  Run URL: {run_url}")
    console.print()

    # Create a Grafana-style alert with tracer information
    grafana_alert = {
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "PipelineFailure",
                    "severity": "critical",
                    "table": pipeline_name,
                    "pipeline_name": pipeline_name,
                    "run_id": trace_id or "",
                    "run_name": run_name,
                    "environment": "production",
                },
                "annotations": {
                    "summary": f"Pipeline {pipeline_name} failed",
                    "description": f"Pipeline {pipeline_name} run {run_name} failed with status {status}",
                    "runbook_url": run_url or "",
                },
                "startsAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": run_url or "",
                "fingerprint": trace_id or "unknown",
            }
        ],
        "groupLabels": {
            "alertname": "PipelineFailure",
        },
        "commonLabels": {
            "alertname": "PipelineFailure",
            "severity": "critical",
            "pipeline_name": pipeline_name,
        },
        "commonAnnotations": {
            "summary": f"Pipeline {pipeline_name} failed",
        },
        "externalURL": os.getenv("TRACER_WEB_APP_URL", "https://staging.tracer.cloud"),
        "version": "4",
        "groupKey": "{}:{alertname=\"PipelineFailure\"}",
        "truncatedAlerts": 0,
        "title": f"[FIRING:1] PipelineFailure critical - {pipeline_name}",
        "state": "alerting",
        "message": f"**Firing**\n\nPipeline {pipeline_name} failed\nRun: {run_name}\nStatus: {status}\nTrace ID: {trace_id}",
    }

    # Create raw alert with Grafana format and tracer context
    raw_alert = grafana_alert.copy()
    raw_alert["run_url"] = run_url
    raw_alert["pipeline_name"] = pipeline_name
    raw_alert["run_name"] = run_name
    raw_alert["trace_id"] = trace_id

    # Create alert details for the pipeline
    alert_name = f"Pipeline failure: {pipeline_name}"
    affected_table = pipeline_name  # Use pipeline name as affected table
    severity = "critical"

    console.print("[bold cyan]🚀 Starting investigation pipeline...[/]")
    console.print()

    # Parse the Grafana alert to show it properly
    from src.ingest import parse_grafana_payload  # noqa: E402

    try:
        request = parse_grafana_payload(grafana_alert)
        alert_name = request.alert_name
        affected_table = request.affected_table
        severity = request.severity
    except Exception:
        # Fallback if parsing fails
        pass

    # Run the full investigation pipeline
    state = run_investigation_pipeline(
        alert_name=alert_name,
        affected_table=affected_table,
        severity=severity,
        raw_alert=raw_alert,
    )

    # Display final results
    console.print()
    console.print("[bold green]✅ Investigation Complete[/]")
    console.print()

    # Show root cause
    root_cause = state.get("root_cause", "")
    confidence = state.get("confidence", 0.0)

    if root_cause:
        console.print("[bold]Root Cause Analysis:[/]")
        console.print(f"[dim]Confidence: {confidence:.0%}[/]")
        console.print()
        # Print root cause with proper formatting
        for line in root_cause.split("\n"):
            if line.strip():
                console.print(f"  {line.strip()}")

    # Show evidence summary
    evidence = state.get("evidence", {})
    web_run_evidence = evidence.get("tracer_web_run", {})
    if web_run_evidence.get("found"):
        failed_jobs = web_run_evidence.get("failed_jobs", [])
        failed_tools = web_run_evidence.get("failed_tools", [])
        console.print()
        console.print("[bold]Evidence Summary:[/]")
        console.print(f"  Failed jobs: {len(failed_jobs)}")
        console.print(f"  Failed tools: {len(failed_tools)}")
        if web_run_evidence.get("run_url"):
            console.print(f"  [dim]View run:[/] [blue underline]{web_run_evidence.get('run_url')}[/]")

    return state


if __name__ == "__main__":
    run_demo()
