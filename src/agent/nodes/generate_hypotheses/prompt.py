"""Prompt building for hypothesis generation."""

from src.agent.nodes.frame_problem.service_graph import render_tools_briefing
from src.agent.state import EvidenceSource, InvestigationState


def build_hypothesis_prompt(
    state: InvestigationState,
    available_sources: list[EvidenceSource],
    recommendations: list[str] | None = None,
    executed_hypotheses: list[dict] | None = None,
) -> str:
    """Build the prompt for hypothesis generation."""
    problem_md = state.get("problem_md", "")
    tools_briefing = render_tools_briefing()

    # Map evidence sources to their names for the prompt
    source_names = {
        "batch": "batch",
        "tracer_web": "tracer_web",
        "cloudwatch": "cloudwatch (AWS CloudWatch metrics and logs)",
    }
    available_sources_list = ", ".join([source_names.get(s, s) for s in available_sources])

    recommendations_section = ""
    if recommendations:
        recommendations_section = f"""

INVESTIGATION RECOMMENDATIONS (from previous validation):
The previous analysis had low confidence. These AWS SDK investigations are recommended:
{chr(10).join(f"- {rec}" for rec in recommendations[:5])}

Consider prioritizing evidence sources that would help validate these recommendations.
"""

    executed_history_section = ""
    if executed_hypotheses:
        executed_history_section = f"""

ALREADY EXECUTED HYPOTHESES (DO NOT REPEAT):
The following evidence sources have already been investigated in previous loops:
{chr(10).join(f"- Loop {h.get('loop_count', 0)}: {', '.join(h.get('sources', []))} - {h.get('rationale', '')[:100]}" for h in executed_hypotheses[-5:])}

IMPORTANT: Do NOT select the same evidence sources that have already been executed.
Focus on NEW evidence sources that haven't been checked yet.
If all sources have been executed, select the most promising one for deeper investigation.
"""

    return f"""You are planning an investigation for a data pipeline alert.

Alert:
- alert_name: {state.get("alert_name", "Unknown")}
- affected_table: {state.get("affected_table", "Unknown")}
- severity: {state.get("severity", "Unknown")}

Problem context (if available):
{problem_md}

Available evidence sources:
{tools_briefing}
{recommendations_section}
{executed_history_section}
IMPORTANT: Only select from these available sources: {available_sources_list}
Do NOT select "storage" or "s3" as these are not available.
Do NOT select sources that have already been executed (see history above).

Select NEW evidence sources that haven't been checked yet and are most useful for this alert.
Return the ordered list in plan_sources and explain why in rationale.
"""
