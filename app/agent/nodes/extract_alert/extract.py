"""LLM-based alert extraction for the frame_problem node."""

import json
from typing import Any, cast

from app.agent.nodes.extract_alert.models import AlertDetails, AlertExtractionInput
from app.agent.output import debug_print
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm


def extract_alert_details(state: InvestigationState) -> AlertDetails:
    """Use the LLM to extract alert details from raw input."""
    raw_alert = state.get("raw_alert")
    if raw_alert is None:
        raise RuntimeError("raw_alert is required for alert extraction")

    input_data = AlertExtractionInput(raw_alert=_format_raw_alert(raw_alert))
    prompt = _build_extraction_prompt(input_data.raw_alert)
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(AlertDetails)
        details = structured_llm.with_config(run_name="LLM – Extract alert fields").invoke(prompt)
    except Exception as err:
        debug_print(f"LLM alert extraction failed, using fallback: {err}")
        return _fallback_details(state, raw_alert)

    if details is None:
        raise RuntimeError("LLM returned no alert details")

    return cast(AlertDetails, details)


def _fallback_details(state: InvestigationState, raw_alert: str | dict[str, Any]) -> AlertDetails:
    """Best-effort extraction without LLM when it fails."""
    alert_name = state.get("alert_name", "unknown")
    pipeline_name = state.get("pipeline_name", "unknown")
    severity = state.get("severity", "unknown")

    if isinstance(raw_alert, dict):
        labels = raw_alert.get("labels", {})
        annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
        alert_name = labels.get("alertname", alert_name)
        pipeline_name = (
            labels.get("pipeline")
            or annotations.get("pipeline_name")
            or raw_alert.get("pipeline_name")
            or pipeline_name
        )
        severity = labels.get("severity", severity)

    return AlertDetails(
        alert_name=alert_name or "unknown",
        pipeline_name=pipeline_name or "unknown",
        severity=severity or "unknown",
        environment=None,
        summary=None,
    )


def _format_raw_alert(raw_alert: str | dict[str, Any]) -> str:
    """Format raw alert input as a string for the LLM."""
    if isinstance(raw_alert, str):
        return raw_alert
    return json.dumps(raw_alert, indent=2, sort_keys=True)


def _build_extraction_prompt(raw_alert: str) -> str:
    """Build the prompt for extracting alert details."""
    return f"""You extract alert metadata from raw input.
The input may be raw text or JSON. Extract:
- alert_name
- pipeline_name
- severity
- environment (if present)
- summary (if present)

Raw alert:
{raw_alert}
"""
