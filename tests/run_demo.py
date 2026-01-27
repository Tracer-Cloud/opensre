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

# Load .env file from project root
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)

load_dotenv()

from langsmith import traceable  # noqa: E402

from app.agent.graph_pipeline import run_investigation  # noqa: E402
from app.agent.nodes.build_context.context_building import (  # noqa: E402
    _fetch_tracer_web_run_context,
)
from app.agent.output import reset_tracker  # noqa: E402
from tests.alert_factory import create_alert_from_tracer_run  # noqa: E402


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

    # Create a Grafana-style alert with tracer information using the factory
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    raw_alert = create_alert_from_tracer_run(
        pipeline_name=pipeline_name,
        run_name=run_name,
        status=status,
        timestamp=timestamp,
        trace_id=trace_id,
        run_url=run_url,
    )

    _print("Starting investigation pipeline...")
    _print("")

    # Parse the Grafana alert to extract structured details
    from app.ingest import parse_grafana_payload  # noqa: E402

    try:
        request = parse_grafana_payload(raw_alert)
        alert_name = request.alert_name
        pipeline_name = request.pipeline_name
        severity = request.severity
    except Exception:
        # Fallback values if parsing fails
        alert_name = f"Pipeline failure: {pipeline_name}"
        pipeline_name = pipeline_name
        severity = "critical"

    # Run the pipeline - publish_findings node handles rendering
    state = run_investigation(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=raw_alert,
    )

    return state


if __name__ == "__main__":
    run_demo()
