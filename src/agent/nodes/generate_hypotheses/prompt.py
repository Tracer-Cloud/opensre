"""Prompt building for hypothesis generation."""

from src.agent.context.service_graph import render_tools_briefing
from src.agent.state import EvidenceSource, InvestigationState


def build_hypothesis_prompt(state: InvestigationState, available_sources: list[EvidenceSource]) -> str:
    """Build the prompt for hypothesis generation."""
    problem_md = state.get("problem_md", "")
    tools_briefing = render_tools_briefing()

    # Map evidence sources to their names for the prompt
    source_names = {
        "tracer": "tracer",
        "batch": "batch",
        "tracer_web": "tracer_web",
    }
    available_sources_list = ", ".join([source_names.get(s, s) for s in available_sources])

    return f"""You are planning an investigation for a data pipeline alert.

Alert:
- alert_name: {state.get("alert_name", "Unknown")}
- affected_table: {state.get("affected_table", "Unknown")}
- severity: {state.get("severity", "Unknown")}

Problem context (if available):
{problem_md}

Available evidence sources:
{tools_briefing}

IMPORTANT: Only select from these available sources: {available_sources_list}
Do NOT select "storage" or "s3" as these are not available.

Select the evidence sources that are most useful for this alert.
Return the ordered list in plan_sources and explain why in rationale.
"""
