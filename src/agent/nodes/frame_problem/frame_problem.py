"""Frame the problem and enrich context.

This node extracts alert details, builds context, and generates a problem statement.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable
from pydantic import BaseModel, Field

from src.agent.nodes.frame_problem.context_building import build_investigation_context
from src.agent.nodes.frame_problem.extract import extract_alert_details
from src.agent.nodes.frame_problem.render import render_problem_statement_md
from src.agent.nodes.frame_problem.service_graph import render_tools_briefing
from src.agent.output import debug_print, get_tracker, render_investigation_header
from src.agent.state import InvestigationState
from src.agent.tools.llm import get_llm


def main(state: InvestigationState) -> dict:
    """
    Main entry point for framing the problem.

    This keeps the core flow easy to follow:
    1) Extract alert fields from raw input using the LLM
    2) Show the investigation header
    3) Generate a structured problem statement
    4) Return parsed alert JSON for downstream nodes
    """
    tracker = get_tracker()
    tracker.start("frame_problem", "Extracting alert details")

    alert_details = extract_alert_details(state)
    debug_print(
        f"Alert: {alert_details.alert_name} | "
        f"Table: {alert_details.affected_table} | "
        f"Severity: {alert_details.severity}"
    )

    render_investigation_header(
        alert_details.alert_name,
        alert_details.affected_table,
        alert_details.severity,
    )

    enriched_state: InvestigationState = {
        **state,
        "alert_name": alert_details.alert_name,
        "affected_table": alert_details.affected_table,
        "severity": alert_details.severity,
    }

    # Gather initial investigation context (metadata) upstream
    context = build_investigation_context({"plan_sources": ["tracer_web"]})  # Always get tracer_web context

    # Store context in state
    enriched_state["evidence"] = context

    problem = _generate_output_problem_statement(enriched_state)
    problem = _add_tools_briefing(problem)
    problem_md = render_problem_statement_md(problem, enriched_state)
    debug_print(f"Problem statement generated ({len(problem_md)} chars)")

    tracker.complete(
        "frame_problem",
        fields_updated=["alert_name", "affected_table", "severity", "evidence", "problem_md"],
    )

    return {
        "alert_name": alert_details.alert_name,
        "affected_table": alert_details.affected_table,
        "severity": alert_details.severity,
        "alert_json": alert_details.model_dump(),
        "problem_md": problem_md,
        "evidence": context,
    }


@traceable(name="node_frame_problem")
def node_frame_problem(state: InvestigationState) -> dict:
    """
    LangGraph node wrapper with LangSmith tracking.

    Kept for graph wiring; delegates to the main flow.
    """
    return main(state)


class ProblemStatement(BaseModel):
    """Structured problem statement for the investigation."""

    summary: str = Field(description="One-line summary of the problem")
    context: str = Field(description="Background context about the alert and affected systems")
    investigation_goals: list[str] = Field(description="Specific goals for the investigation")
    constraints: list[str] = Field(description="Known constraints or limitations")


def _build_input_prompt(state: InvestigationState) -> str:
    """Build the prompt for generating a problem statement."""
    return f"""You are framing a data pipeline incident for investigation.

Alert Information:
- alert_name: {state.get("alert_name", "Unknown")}
- affected_table: {state.get("affected_table", "Unknown")}
- severity: {state.get("severity", "Unknown")}

Task:
Analyze the alert and provide a structured problem statement.
"""


def _generate_output_problem_statement(state: InvestigationState) -> ProblemStatement:
    """Use the LLM to generate a structured problem statement."""
    prompt = _build_input_prompt(state)
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(ProblemStatement)
        problem = structured_llm.invoke(prompt)
    except Exception as err:
        raise RuntimeError("Failed to generate problem statement") from err

    if problem is None:
        raise RuntimeError("LLM returned no problem statement")

    return problem


def _add_tools_briefing(problem: ProblemStatement) -> ProblemStatement:
    """Add a tools briefing to the problem context."""
    if "Available evidence sources" in problem.context:
        return problem
    new_context = f"{problem.context}\n\n{render_tools_briefing()}"
    return problem.model_copy(update={"context": new_context})
