"""Extract alert details and seed investigation state."""

import json
import logging
import time
from typing import Any

from langsmith import traceable

from app.agent.nodes.extract_alert.extract import extract_alert_details
from app.agent.nodes.extract_alert.models import AlertDetails
from app.agent.output import debug_print, get_tracker, render_investigation_header
from app.agent.state import InvestigationState

logger = logging.getLogger(__name__)


def _make_problem_md(details: AlertDetails) -> str:
    parts = [f"# {details.alert_name}", f"Pipeline: {details.pipeline_name} | Severity: {details.severity}"]
    if details.kube_namespace:
        parts.append(f"Namespace: {details.kube_namespace}")
    if details.error_message:
        parts.append(f"\nError: {details.error_message}")
    return "\n".join(parts)


def _enrich_raw_alert(raw_alert: Any, details: AlertDetails) -> Any:
    """Inject LLM-extracted structured fields into raw_alert dict so detect_sources can find them."""
    if not isinstance(raw_alert, dict):
        return raw_alert
    enriched = dict(raw_alert)
    if details.kube_namespace:
        enriched["kube_namespace"] = details.kube_namespace
    if details.cloudwatch_log_group:
        enriched["cloudwatch_log_group"] = details.cloudwatch_log_group
    if details.error_message:
        enriched["error_message"] = details.error_message
    return enriched


@traceable(name="node_extract_alert")
def node_extract_alert(state: InvestigationState) -> dict:
    """Classify and extract alert details from raw input (single LLM call)."""
    tracker = get_tracker()
    tracker.start("extract_alert", "Classifying and extracting alert details")

    raw_input = state.get("raw_alert")
    if raw_input is not None:
        formatted = json.dumps(raw_input, indent=2, default=str) if isinstance(raw_input, dict) else str(raw_input)
        logger.info("[extract_alert] Raw alert input:\n%s", formatted)
        debug_print(f"Raw alert input:\n{formatted}")

    details = extract_alert_details(state)

    if details.is_noise:
        debug_print("Message classified as noise - skipping investigation")
        tracker.complete("extract_alert", fields_updated=["is_noise"])
        return {"is_noise": True}

    raw_alert = state.get("raw_alert", {})
    alert_id = raw_alert.get("alert_id") if isinstance(raw_alert, dict) else None

    debug_print(
        f"Alert: {details.alert_name} | Pipeline: {details.pipeline_name} | "
        f"Severity: {details.severity} | namespace={details.kube_namespace} | Alert ID: {alert_id}"
    )

    render_investigation_header(details.alert_name, details.pipeline_name, details.severity, alert_id=alert_id)

    # Enrich raw_alert with LLM-extracted structured fields so detect_sources can find them
    enriched_alert = _enrich_raw_alert(raw_alert, details)

    tracker.complete("extract_alert", fields_updated=["alert_name", "pipeline_name", "severity", "alert_json", "problem_md", "raw_alert"])

    result: dict = {
        "is_noise": False,
        "alert_name": details.alert_name,
        "pipeline_name": details.pipeline_name,
        "severity": details.severity,
        "alert_json": details.model_dump(),
        "raw_alert": enriched_alert,
        "problem_md": _make_problem_md(details),
    }
    if not state.get("investigation_started_at"):
        result["investigation_started_at"] = time.monotonic()
    return result
