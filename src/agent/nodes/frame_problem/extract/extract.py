"""LLM-based alert extraction for the frame_problem node."""

import json
from typing import Any

from src.agent.nodes.frame_problem.extract.models import AlertDetails, AlertExtractionInput
from src.agent.state import InvestigationState
from src.agent.tools.llm import get_llm


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
        details = structured_llm.invoke(prompt)
    except Exception as err:
        raise RuntimeError("Failed to extract alert details") from err

    if details is None:
        raise RuntimeError("LLM returned no alert details")

    return details


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
- affected_table
- severity
- environment (if present)
- summary (if present)

Raw alert:
{raw_alert}
"""
