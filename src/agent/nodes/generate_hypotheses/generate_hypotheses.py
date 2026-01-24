"""Generate investigation hypotheses based on alert context."""


from pydantic import BaseModel, Field

from src.agent.nodes.generate_hypotheses.prompt import build_hypothesis_prompt
from src.agent.nodes.publish_findings.render import (
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


def _get_available_sources() -> list[EvidenceSource]:
    """Get list of evidence sources that are actually available."""
    # S3/storage is not implemented, so exclude it
    return ["tracer", "batch", "tracer_web"]


def main(state: InvestigationState) -> dict:
    """
    Main entry point for hypothesis generation.

    Flow:
    1) Check which evidence sources are available
    2) Generate hypothesis plan using LLM (only from available sources)
    3) Ensure required sources are present
    4) Render the plan with rationale
    """
    render_step_header(1, "Generate hypotheses")

    # Filter to only available sources before generating plan
    available_sources = _get_available_sources()
    plan = _generate_hypothesis_plan(state, available_sources)

    # Filter plan_sources to only include available sources
    plan_sources = [s for s in plan.plan_sources if s in available_sources]
    plan_sources = _ensure_required_sources(plan_sources)

    render_plan(plan_sources, rationale=plan.rationale)

    return {"plan_sources": plan_sources}


def node_generate_hypotheses(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    return main(state)


def _generate_hypothesis_plan(state: InvestigationState, available_sources: list[EvidenceSource]) -> HypothesisPlan:
    """Use the LLM to select evidence sources from available sources only."""
    prompt = build_hypothesis_prompt(state, available_sources)
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(HypothesisPlan)
        plan = structured_llm.invoke(prompt)
    except Exception as err:
        raise RuntimeError("Failed to generate hypothesis plan") from err

    if plan is None or not plan.plan_sources:
        raise RuntimeError("LLM returned no hypothesis plan")

    return plan


def _ensure_required_sources(plan_sources: list[EvidenceSource]) -> list[EvidenceSource]:
    """Ensure required sources are included without duplicating."""
    required_sources: list[EvidenceSource] = ["tracer_web"]
    ordered = list(plan_sources)
    for source in required_sources:
        if source not in ordered:
            ordered.append(source)
    return ordered

