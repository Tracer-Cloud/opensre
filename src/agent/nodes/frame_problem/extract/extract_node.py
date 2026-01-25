"""Extract alert details and seed investigation state."""

from langsmith import traceable

from src.agent.nodes.frame_problem.extract.extract import extract_alert_details
from src.agent.output import debug_print, get_tracker, render_investigation_header
from src.agent.state import InvestigationState


@traceable(name="node_frame_problem_extract")
def node_frame_problem_extract(state: InvestigationState) -> dict:
    """
    Extract alert details from raw input and render the investigation header.
    """
    tracker = get_tracker()
    tracker.start("frame_problem_extract", "Extracting alert details")

    alert_details = extract_alert_details(state)
    debug_print(
        f"Alert: {alert_details.alert_name} | "
        f"Table: {alert_details.affected_table} | "
        f"Severity: {alert_details.severity}"
    )

    render_investigation_header(
        alert_details.alert_name,
        alert_details.affected_table,
        alert_details.severity,
    )

    tracker.complete(
        "frame_problem_extract",
        fields_updated=["alert_name", "affected_table", "severity", "alert_json"],
    )

    return {
        "alert_name": alert_details.alert_name,
        "affected_table": alert_details.affected_table,
        "severity": alert_details.severity,
        "alert_json": alert_details.model_dump(),
    }
