"""Frame the problem and enrich context."""

from pydantic import BaseModel, Field

from src.agent.nodes.frame_problem.extract import AlertDetails, extract_alert_details
from src.agent.nodes.frame_problem.render import render_problem_statement_md
from src.agent.nodes.publish_findings.render import (
    console,
    render_investigation_start,
    render_step_header,
)
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
    render_step_header(1, "Extract alert details")
    alert_details = extract_alert_details(state)
    console.print(
        f"  [dim]Alert:[/] {alert_details.alert_name} | "
        f"[dim]Table:[/] {alert_details.affected_table} | "
        f"[dim]Severity:[/] {alert_details.severity}"
    )

    render_investigation_start(
        alert_details.alert_name,
        alert_details.affected_table,
        alert_details.severity,
    )

    render_step_header(2, "Frame problem statement")

    enriched_state: InvestigationState = {
        **state,
        "alert_name": alert_details.alert_name,
        "affected_table": alert_details.affected_table,
        "severity": alert_details.severity,
    }
    problem = _generate_output_problem_statement(enriched_state)
    problem_md = render_problem_statement_md(problem, enriched_state)
    render_step_header(4, "Problem statement output")
    console.print(problem_md)

    return {
        "alert_name": alert_details.alert_name,
        "affected_table": alert_details.affected_table,
        "severity": alert_details.severity,
        "alert_json": alert_details.model_dump(),
        "problem_md": problem_md,
    }


def node_frame_problem(state: InvestigationState) -> dict:
    """
    LangGraph node wrapper.

    Kept for graph wiring; delegates to the main flow.
    """
    return main(state)


class ProblemStatement(BaseModel):
    """Structured problem statement for the investigation."""

    summary: str = Field(description="One-line summary of the problem")
    context: str = Field(
        description="Background context about the alert and affected systems"
    )
    investigation_goals: list[str] = Field(
        description="Specific goals for the investigation"
    )
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
    render_step_header(3, "Generate problem statement")
    prompt = _build_input_prompt(state)
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(ProblemStatement)
        problem = structured_llm.invoke(prompt)
    except Exception:
        raise RuntimeError("Failed to generate problem statement")

    if problem is None:
        raise RuntimeError("LLM returned no problem statement")

    return problem

