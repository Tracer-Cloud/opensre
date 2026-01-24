"""
Demo runner for the incident resolution agent.

Run with: python -m tests.run_demo

Rendering is handled in the ingestion layer and nodes.
Uses the same pipeline runner as the CLI.
"""
from pathlib import Path

from config import init_runtime

init_runtime()

from langsmith import traceable

from src.agent.graph_pipeline import run_investigation_pipeline
from src.ingest import load_request_from_json

# Path to fixture
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "grafana_alert.json"

@traceable
def run_demo():
    """Run the LangGraph incident resolution demo with Rich console output."""
    # Load alert from test fixture
    request = load_request_from_json(str(FIXTURE_PATH))

    return run_investigation_pipeline(
        alert_name=request.alert_name,
        affected_table=request.affected_table,
        severity=request.severity,
        raw_alert=request.raw_alert,
    )


if __name__ == "__main__":
    run_demo()

