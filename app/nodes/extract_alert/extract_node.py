"""Extract alert details and seed investigation state."""

import json
import logging
import time
from typing import Any

from langsmith import traceable

from app.nodes.extract_alert.extract import extract_alert_details
from app.nodes.extract_alert.models import AlertDetails
from app.output import debug_print, get_tracker, render_investigation_header
from app.state import InvestigationState

logger = logging.getLogger(__name__)


def _make_problem_md(details: AlertDetails) -> str:
    parts = [
        f"# {details.alert_name}",
        f"Pipeline: {details.pipeline_name} | Severity: {details.severity}",
    ]
    if details.kube_namespace:
        parts.append(f"Namespace: {details.kube_namespace}")
    if details.error_message:
        parts.append(f"\nError: {details.error_message}")
    return "\n".join(parts)


def _enrich_raw_alert(raw_alert: Any, details: AlertDetails) -> Any:
    """Inject LLM-extracted structured fields into raw_alert dict so detect_sources can find them."""
    if not isinstance(raw_alert, dict):
        # Convert string alerts to dict so downstream can find extracted fields
        raw_alert = {}
    enriched = dict(raw_alert)
    if details.kube_namespace:
        enriched["kube_namespace"] = details.kube_namespace
    if details.cloudwatch_log_group:
        enriched["cloudwatch_log_group"] = details.cloudwatch_log_group
    if details.error_message:
        enriched["error_message"] = details.error_message
    if details.alert_source:
        enriched["alert_source"] = details.alert_source
    if details.log_query:
        enriched["log_query"] = details.log_query
    if details.eks_cluster:
        enriched["eks_cluster"] = details.eks_cluster
    if details.pod_name:
        enriched["pod_name"] = details.pod_name
    if details.deployment:
        enriched["deployment"] = details.deployment
    return enriched


@traceable(name="node_extract_alert")
def node_extract_alert(state: InvestigationState) -> dict:
    """Classify and extract alert details from raw input (single LLM call)."""
    tracker = get_tracker()
    tracker.start("extract_alert", "Classifying and extracting alert details")

    raw_input = state.get("raw_alert")
    if raw_input is not None:
        formatted = (
            json.dumps(raw_input, indent=2, default=str)
            if isinstance(raw_input, dict)
            else str(raw_input)
        )
        logger.info("[extract_alert] Raw alert input:\n%s", formatted)
        debug_print(f"Raw alert input:\n{formatted}")

    details = extract_alert_details(state)

    if details.is_noise:
        debug_print("Message classified as noise - skipping investigation")
        tracker.complete("extract_alert", fields_updated=["is_noise"])
        slack_ctx = state.get("slack_context", {}) or {}
        _ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")
        _channel = slack_ctx.get("channel_id")
        _token = slack_ctx.get("access_token")
        if _token and _channel and _ts:
            from app.utils.slack_delivery import swap_reaction

            swap_reaction("eyes", "white_check_mark", _channel, _ts, _token)
        return {"is_noise": True}

    raw_alert = state.get("raw_alert", {})
    alert_id = raw_alert.get("alert_id") if isinstance(raw_alert, dict) else None

    slack_ctx = state.get("slack_context", {}) or {}
    _ts = slack_ctx.get("ts") or slack_ctx.get("thread_ts")
    _channel = slack_ctx.get("channel_id")
    _token = slack_ctx.get("access_token")
    if _token and _channel and _ts:
        from app.utils.slack_delivery import add_reaction

        add_reaction("eyes", _channel, _ts, _token)

    debug_print(
        f"Alert: {details.alert_name} | Pipeline: {details.pipeline_name} | "
        f"Severity: {details.severity} | namespace={details.kube_namespace} | Alert ID: {alert_id}"
    )

    render_investigation_header(
        details.alert_name, details.pipeline_name, details.severity, alert_id=alert_id
    )

    enriched_alert = _enrich_raw_alert(raw_alert, details)

    tracker.complete(
        "extract_alert",
        fields_updated=[
            "alert_name",
            "pipeline_name",
            "severity",
            "alert_source",
            "alert_json",
            "problem_md",
            "raw_alert",
        ],
    )

    result: dict = {
        "is_noise": False,
        "alert_name": details.alert_name,
        "pipeline_name": details.pipeline_name,
        "severity": details.severity,
        "alert_json": details.model_dump(),
        "raw_alert": enriched_alert,
        "problem_md": _make_problem_md(details),
    }
    if details.alert_source:
        result["alert_source"] = details.alert_source
    if not state.get("investigation_started_at"):
        result["investigation_started_at"] = time.monotonic()
    return result
