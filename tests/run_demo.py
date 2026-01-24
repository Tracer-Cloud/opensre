#!/usr/bin/env python3
"""
Demo runner for the incident resolution agent.

Run with: python tests/run_demo.py

Rendering is handled in the ingestion layer and nodes.
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

from src.agent.graph_pipeline import build_graph  # noqa: E402
from src.agent.state import make_initial_state  # noqa: E402
from src.ingest import parse_grafana_payload  # noqa: E402

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

def load_sample_request():
    """Load the sample alert from test fixtures and parse into InvestigationRequest."""
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    return parse_grafana_payload(data, raw_alert_text=RAW_ALERT_TEXT)


@traceable
def run_demo():
    """Run the LangGraph incident resolution demo with Rich console output."""
    # Load alert from test fixture
    request = load_sample_request()

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
        for node_output in event.values():
            accumulated_state.update(node_output)

    final_state = accumulated_state

    return final_state


if __name__ == "__main__":
    run_demo()

