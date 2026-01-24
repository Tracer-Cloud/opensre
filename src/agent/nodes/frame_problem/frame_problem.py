"""Frame the problem and enrich context.

This node extracts alert details, builds context, and generates a problem statement.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable

from src.agent.nodes.frame_problem.context_building import build_investigation_context
from src.agent.nodes.frame_problem.extract import extract_alert_details
from src.agent.nodes.frame_problem.models import ProblemStatement, ProblemStatementInput
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
    _log_alert_details(alert_details)
    _render_header(alert_details)

    enriched_state = _enrich_state_with_alert(state, alert_details)
    context = build_investigation_context(enriched_state)
    enriched_state = _merge_context(enriched_state, context)

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
        "evidence": enriched_state.get("evidence", context),
    }


@traceable(name="node_frame_problem")
def node_frame_problem(state: InvestigationState) -> dict:
    """
    LangGraph node wrapper with LangSmith tracking.

    Kept for graph wiring; delegates to the main flow.
    """
    return main(state)


def _build_input_prompt(problem_input: ProblemStatementInput) -> str:
    """Build the prompt for generating a problem statement."""
    return f"""You are framing a data pipeline incident for investigation.

Alert Information:
- alert_name: {problem_input.alert_name}
- affected_table: {problem_input.affected_table}
- severity: {problem_input.severity}

Task:
Analyze the alert and provide a structured problem statement.
"""


def _generate_output_problem_statement(state: InvestigationState) -> ProblemStatement:
    """Use the LLM to generate a structured problem statement."""
    prompt = _build_input_prompt(ProblemStatementInput.from_state(state))
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


def _log_alert_details(alert_details) -> None:
    debug_print(
        f"Alert: {alert_details.alert_name} | "
        f"Table: {alert_details.affected_table} | "
        f"Severity: {alert_details.severity}"
    )


def _render_header(alert_details) -> None:
    render_investigation_header(
        alert_details.alert_name,
        alert_details.affected_table,
        alert_details.severity,
    )


def _enrich_state_with_alert(
    state: InvestigationState,
    alert_details,
) -> InvestigationState:
    return {
        **state,
        "alert_name": alert_details.alert_name,
        "affected_table": alert_details.affected_table,
        "severity": alert_details.severity,
    }


def _merge_context(state: InvestigationState, context: dict) -> InvestigationState:
    evidence = {
        **state.get("evidence", {}),
        **context,
    }
    return {**state, "evidence": evidence}
