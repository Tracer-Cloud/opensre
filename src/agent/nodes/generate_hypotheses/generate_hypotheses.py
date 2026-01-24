"""Generate investigation hypotheses based on alert context."""

from pydantic import BaseModel, Field

from src.agent.nodes.generate_hypotheses.prompt import build_hypothesis_prompt
from src.agent.nodes.rca_report_publishing.render import (
    render_plan,
    render_step_header,
)
from src.agent.state import EvidenceSource, InvestigationState
from src.agent.tools.llm import get_llm
from src.agent.utils import get_executed_sources


class HypothesisPlan(BaseModel):
    """Structured plan for evidence sources to check."""

    plan_sources: list[EvidenceSource] = Field(
        description="Ordered list of evidence sources to check"
    )
    rationale: str = Field(description="Reasoning for the chosen sources")


def _get_available_sources() -> list[EvidenceSource]:
    """Get list of evidence sources that are actually available."""
    # S3/storage is not implemented, so exclude it
    return ["batch", "tracer_web", "cloudwatch"]


def main(state: InvestigationState) -> dict:
    """
    Main entry point for hypothesis generation.

    Flow:
    1) Check which evidence sources are available
    2) Check for investigation recommendations from validation
    3) Generate hypothesis plan using LLM (only from available sources)
    4) Ensure required sources are present
    5) Render the plan with rationale
    """
    render_step_header(1, "Generate hypotheses")

    # Check if we have investigation recommendations from validation
    investigation_recommendations = state.get("investigation_recommendations", [])
    if investigation_recommendations:
        from src.agent.nodes.rca_report_publishing.render import console

        console.print("\n  [yellow]📋 Investigation Recommendations (from validation):[/]")
        for i, rec in enumerate(investigation_recommendations[:5], 1):
            console.print(f"    {i}. {rec}")
        console.print()

    # Get executed hypotheses history to avoid duplicates
    executed_hypotheses = state.get("executed_hypotheses", [])
    executed_sources_set = get_executed_sources(state)

    # Filter to only available sources before generating plan
    available_sources = _get_available_sources()

    # STRICT: Filter out ALL already executed sources - no exceptions
    available_sources_filtered = [s for s in available_sources if s not in executed_sources_set]

    # If no sources available, we cannot continue - this should not happen with max 1 loop
    if not available_sources_filtered:
        from src.agent.nodes.rca_report_publishing.render import console

        console.print(
            "  [red]⚠️  All available sources have been executed. Cannot gather new evidence.[/]"
        )
        console.print("  [yellow]Proceeding with existing evidence.[/]")
        # Return empty plan - this will cause the graph to proceed with existing evidence
        return {
            "plan_sources": [],
            "executed_hypotheses": executed_hypotheses,
        }

    plan = _generate_hypothesis_plan(
        state, available_sources_filtered, investigation_recommendations, executed_hypotheses
    )

    # Filter plan_sources to only include available sources that haven't been executed
    plan_sources = [s for s in plan.plan_sources if s in available_sources_filtered]

    # Remove any sources that were already executed (safety check)
    plan_sources = [s for s in plan_sources if s not in executed_sources_set]

    # Only ensure required sources if we have a plan - don't force tracer_web if already executed
    if plan_sources:
        plan_sources = _ensure_required_sources(plan_sources, executed_sources_set)
    else:
        from src.agent.nodes.rca_report_publishing.render import console

        console.print("  [yellow]⚠️  No new sources selected. All sources have been executed.[/]")

    # Track this hypothesis execution
    new_hypothesis = {
        "source": plan_sources[0] if plan_sources else None,
        "sources": plan_sources,
        "rationale": plan.rationale,
        "loop_count": state.get("investigation_loop_count", 0),
    }
    executed_hypotheses = executed_hypotheses + [new_hypothesis]

    render_plan(plan_sources, rationale=plan.rationale)

    return {
        "plan_sources": plan_sources,
        "executed_hypotheses": executed_hypotheses,
    }


def node_generate_hypotheses(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    return main(state)


def _generate_hypothesis_plan(
    state: InvestigationState,
    available_sources: list[EvidenceSource],
    recommendations: list[str] | None = None,
    executed_hypotheses: list[dict] | None = None,
) -> HypothesisPlan:
    """Use the LLM to select evidence sources from available sources only."""
    prompt = build_hypothesis_prompt(state, available_sources, recommendations, executed_hypotheses)
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(HypothesisPlan)
        plan = structured_llm.invoke(prompt)
    except Exception as err:
        raise RuntimeError("Failed to generate hypothesis plan") from err

    if plan is None or not plan.plan_sources:
        raise RuntimeError("LLM returned no hypothesis plan")

    return plan


def _ensure_required_sources(
    plan_sources: list[EvidenceSource], executed_sources: set[str] | None = None
) -> list[EvidenceSource]:
    """
    Ensure required sources are included without duplicating.

    Only adds required sources if they haven't been executed yet.
    """
    if executed_sources is None:
        executed_sources = set()

    required_sources: list[EvidenceSource] = ["tracer_web"]
    ordered = list(plan_sources)
    for source in required_sources:
        # Only add if not already in plan AND not already executed
        if source not in ordered and source not in executed_sources:
            ordered.append(source)
    return ordered
