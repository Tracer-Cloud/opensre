"""Extract alert details and seed investigation state."""

import json
import logging
import time

from langsmith import traceable

from app.agent.nodes.extract_alert.extract import classify_alert, extract_alert_details
from app.agent.output import debug_print, get_tracker, render_investigation_header
from app.agent.state import InvestigationState

logger = logging.getLogger(__name__)


@traceable(name="node_extract_alert")
def node_extract_alert(state: InvestigationState) -> dict:
    """
    Classify and extract alert details from raw input.

    First determines if the message is a real alert worth investigating.
    If noise, sets is_noise=True so the graph can skip the investigation.
    """
    tracker = get_tracker()
    tracker.start("extract_alert", "Classifying and extracting alert details")

    # Log the full raw alert input for debugging
    raw_input = state.get("raw_alert")
    if raw_input is not None:
        formatted = json.dumps(raw_input, indent=2, default=str) if isinstance(raw_input, dict) else str(raw_input)
        logger.info("[extract_alert] Raw alert input:\n%s", formatted)
        debug_print(f"Raw alert input:\n{formatted}")

    # Classify first - skip full extraction if noise
    if not classify_alert(state):
        debug_print("Message classified as noise - skipping investigation")
        tracker.complete("extract_alert", fields_updated=["is_noise"])
        return {"is_noise": True}

    alert_details = extract_alert_details(state)

    raw_alert = state.get("raw_alert", {})
    alert_id = raw_alert.get("alert_id") if isinstance(raw_alert, dict) else None

    debug_print(
        f"Alert: {alert_details.alert_name} | "
        f"Pipeline: {alert_details.pipeline_name} | "
        f"Severity: {alert_details.severity}"
        f"Alert ID: {alert_id}"
        f"Raw alert: {raw_input}"
    )

    render_investigation_header(
        alert_details.alert_name,
        alert_details.pipeline_name,
        alert_details.severity,
        alert_id=alert_id,
    )

    tracker.complete(
        "extract_alert",
        fields_updated=["alert_name", "pipeline_name", "severity", "alert_json"],
    )

    result: dict = {
        "is_noise": False,
        "alert_name": alert_details.alert_name,
        "pipeline_name": alert_details.pipeline_name,
        "severity": alert_details.severity,
        "alert_json": alert_details.model_dump(),
    }
    # Ensure investigation timer is set (may be missing when invoked via LangGraph)
    if not state.get("investigation_started_at"):
        result["investigation_started_at"] = time.monotonic()
    return result
