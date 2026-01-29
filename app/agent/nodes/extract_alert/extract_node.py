"""Extract alert details and seed investigation state."""

from langsmith import traceable

from app.agent.nodes.extract_alert.extract import extract_alert_details
from app.agent.output import debug_print, get_tracker, render_investigation_header
from app.agent.state import InvestigationState


@traceable(name="node_extract_alert")
def node_extract_alert(state: InvestigationState) -> dict:
    """
    Extract alert details from raw input and render the investigation header.
    """
    tracker = get_tracker()
    tracker.start("extract_alert", "Extracting alert details")

    alert_details = extract_alert_details(state)

    raw_alert = state.get("raw_alert", {})
    alert_id = raw_alert.get("alert_id") if isinstance(raw_alert, dict) else None

    debug_print(
        f"Alert: {alert_details.alert_name} | "
        f"Pipeline: {alert_details.pipeline_name} | "
        f"Severity: {alert_details.severity}"
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

    return {
        "alert_name": alert_details.alert_name,
        "pipeline_name": alert_details.pipeline_name,
        "severity": alert_details.severity,
        "alert_json": alert_details.model_dump(),
    }
