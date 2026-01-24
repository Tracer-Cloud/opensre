"""LLM-based alert extraction for the frame_problem node."""

import json
from typing import Any

from pydantic import BaseModel, Field

from src.agent.state import InvestigationState
from src.agent.tools.llm import get_llm


class AlertDetails(BaseModel):
    """Structured alert details extracted from raw input."""

    alert_name: str = Field(description="Name of the alert")
    affected_table: str = Field(description="Primary affected table")
    severity: str = Field(
        description="Severity of the alert (e.g. critical, high, warning, info)"
    )
    environment: str | None = Field(default=None, description="Environment, if present")
    summary: str | None = Field(default=None, description="Short alert summary, if present")


def extract_alert_details(state: InvestigationState) -> AlertDetails:
    """Use the LLM to extract alert details from raw input."""
    raw_alert = state.get("raw_alert")
    if raw_alert is None:
        raise RuntimeError("raw_alert is required for alert extraction")

    prompt = _build_extraction_prompt(_format_raw_alert(raw_alert))
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(AlertDetails)
        details = structured_llm.invoke(prompt)
    except Exception:
        raise RuntimeError("Failed to extract alert details")

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
