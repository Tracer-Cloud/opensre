"""
Demo runner for the incident resolution agent.

Run with: python -m tests.run_demo

This demo:
1. Finds a real failed pipeline run from Tracer Web App
2. Creates an alert for that pipeline
3. Runs full investigation pipeline (which renders the final report)
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from src.agent.constants import TRACER_BASE_URL

# Load .env file from project root
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)

from config import init_runtime  # noqa: E402

init_runtime()

from langsmith import traceable  # noqa: E402

from src.agent.graph_pipeline import run_investigation_pipeline  # noqa: E402
from src.agent.nodes.frame_problem.context.context_building import (  # noqa: E402
    _fetch_tracer_web_run_context,
)
from src.agent.output import reset_tracker  # noqa: E402


def _print(message: str) -> None:
    """Simple print wrapper."""
    print(message)


@traceable
def run_demo():
    """Run the LangGraph incident resolution demo with a real failed pipeline."""
    reset_tracker()

    # Check required environment variables
    api_key = os.getenv("ANTHROPIC_API_KEY")
    jwt_token = os.getenv("JWT_TOKEN")

    if not api_key:
        _print("Error: Missing required environment variable: ANTHROPIC_API_KEY")
        _print(f"\nPlease set this in your .env file at: {env_path}")
        return None

    if not jwt_token:
        _print("Error: Missing required environment variable: JWT_TOKEN")
        _print(f"\nPlease set this in your .env file at: {env_path}")
        return None

    _print("Finding a real failed pipeline run...")

    # Find a real failed run from Tracer Web App
    web_run = _fetch_tracer_web_run_context()

    if not web_run.get("found"):
        _print("No failed runs found in Tracer Web App")
        _print(f"Checked {web_run.get('pipelines_checked', 0)} pipelines")
        return None

    # Extract pipeline details
    pipeline_name = web_run.get("pipeline_name", "unknown_pipeline")
    run_name = web_run.get("run_name", "unknown_run")
    trace_id = web_run.get("trace_id")
    status = web_run.get("status", "unknown")
    run_url = web_run.get("run_url")

    _print(f"Found failed run: {run_name}")
    _print(f"  Pipeline: {pipeline_name}")
    _print(f"  Status: {status}")
    if trace_id:
        _print(f"  Trace ID: {trace_id}")
    if run_url:
        _print(f"  Run URL: {run_url}")
    _print("")

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
        "groupLabels": {"alertname": "PipelineFailure"},
        "commonLabels": {
            "alertname": "PipelineFailure",
            "severity": "critical",
            "pipeline_name": pipeline_name,
        },
        "commonAnnotations": {"summary": f"Pipeline {pipeline_name} failed"},
        "externalURL": TRACER_BASE_URL,
        "version": "4",
        "groupKey": '{}:{alertname="PipelineFailure"}',
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

    # Create alert details
    alert_name = f"Pipeline failure: {pipeline_name}"
    affected_table = pipeline_name
    severity = "critical"

    _print("Starting investigation pipeline...")
    _print("")

    # Parse the Grafana alert
    from src.ingest import parse_grafana_payload  # noqa: E402

    try:
        request = parse_grafana_payload(grafana_alert)
        alert_name = request.alert_name
        affected_table = request.affected_table
        severity = request.severity
    except Exception:
        pass

    # Run the pipeline - publish_findings node handles rendering
    state = run_investigation_pipeline(
        alert_name=alert_name,
        affected_table=affected_table,
        severity=severity,
        raw_alert=raw_alert,
    )

    return state


if __name__ == "__main__":
    run_demo()
