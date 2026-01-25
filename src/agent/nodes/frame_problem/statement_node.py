"""Generate the investigation problem statement."""

from langsmith import traceable

from src.agent.nodes.frame_problem.statement.models import (
    ProblemStatement,
    ProblemStatementInput,
)
from src.agent.nodes.frame_problem.statement.render import (
    render_problem_statement_md,
)
from src.agent.output import debug_print, get_tracker
from src.agent.state import InvestigationState
from src.agent.tools.llm import get_llm


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


@traceable(name="node_frame_problem_statement")
def node_frame_problem_statement(state: InvestigationState) -> dict:
    """Generate and render the problem statement."""
    tracker = get_tracker()
    tracker.start("frame_problem_statement", "Generating problem statement")

    problem = _generate_output_problem_statement(state)
    problem_md = render_problem_statement_md(problem, state)
    debug_print(f"Problem statement generated ({len(problem_md)} chars)")

    tracker.complete("frame_problem_statement", fields_updated=["problem_md"])
    return {"problem_md": problem_md}
