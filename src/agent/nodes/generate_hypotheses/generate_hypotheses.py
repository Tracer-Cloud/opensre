"""Generate investigation hypotheses based on alert context."""

from pydantic import BaseModel, Field

from src.agent.context.service_graph import render_tools_briefing
from src.agent.nodes.publish_findings.render import (
    console,
    render_plan,
    render_step_header,
)
from src.agent.state import EvidenceSource, InvestigationState
from src.agent.tools.llm import get_llm


class HypothesisPlan(BaseModel):
    """Structured plan for evidence sources to check."""

    plan_sources: list[EvidenceSource] = Field(
        description="Ordered list of evidence sources to check"
    )
    rationale: str = Field(description="Reasoning for the chosen sources")


def main(state: InvestigationState) -> dict:
    """
    Main entry point for hypothesis generation.

    Flow:
    1) Ask the LLM to select evidence sources
    2) Ensure required sources are present
    3) Render the plan to the console
    """
    render_step_header(1, "Generate hypotheses")
    plan = _generate_hypothesis_plan(state)
    plan_sources = _ensure_required_sources(plan.plan_sources)

    render_plan(plan_sources)
    console.print(f"  [dim]Rationale:[/] {plan.rationale}")

    return {"plan_sources": plan_sources}


def node_generate_hypotheses(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    return main(state)


def _generate_hypothesis_plan(state: InvestigationState) -> HypothesisPlan:
    """Use the LLM to select evidence sources."""
    prompt = _build_prompt(state)
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(HypothesisPlan)
        plan = structured_llm.invoke(prompt)
    except Exception as err:
        raise RuntimeError("Failed to generate hypothesis plan") from err

    if plan is None or not plan.plan_sources:
        raise RuntimeError("LLM returned no hypothesis plan")

    return plan


def _build_prompt(state: InvestigationState) -> str:
    """Build the prompt for hypothesis generation."""
    problem_md = state.get("problem_md", "")
    tools_briefing = render_tools_briefing()

    return f"""You are planning an investigation for a data pipeline alert.

Alert:
- alert_name: {state.get("alert_name", "Unknown")}
- affected_table: {state.get("affected_table", "Unknown")}
- severity: {state.get("severity", "Unknown")}

Problem context (if available):
{problem_md}

Available evidence sources:
{tools_briefing}

Select the evidence sources that are most useful for this alert.
Return the ordered list in plan_sources and explain why in rationale.
"""


def _ensure_required_sources(plan_sources: list[EvidenceSource]) -> list[EvidenceSource]:
    """Ensure required sources are included without duplicating."""
    required_sources: list[EvidenceSource] = ["tracer_web"]
    ordered = list(plan_sources)
    for source in required_sources:
        if source not in ordered:
            ordered.append(source)
    return ordered

