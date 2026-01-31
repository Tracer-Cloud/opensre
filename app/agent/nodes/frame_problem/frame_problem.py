"""Frame the problem statement.

This node generates a problem statement from extracted alert details and context.
It assumes extract_alert and build_context nodes have already run.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable

from app.agent.nodes.frame_problem.models import (
    ProblemStatement,
    ProblemStatementInput,
)
from app.agent.nodes.frame_problem.render import render_problem_statement_md
from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm


def _build_input_prompt(problem_input: ProblemStatementInput) -> str:
    """Build the prompt for generating a problem statement."""
    return f"""You are framing a data pipeline incident for investigation.

Alert Information:
- alert_name: {problem_input.alert_name}
- pipeline_name: {problem_input.pipeline_name}
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
        chain = structured_llm.with_config(run_name="LLM – Draft problem statement")

        problem = chain.invoke(prompt)
    except Exception as err:
        raise RuntimeError("Failed to generate problem statement") from err

    if problem is None:
        raise RuntimeError("LLM returned no problem statement")

    return problem


@traceable(name="node_frame_problem")
def node_frame_problem(state: InvestigationState) -> dict:
    """
    Generate and render the problem statement.

    Assumes:
    - extract_alert node has already populated alert_name, pipeline_name, severity, alert_json
    - build_context node has already populated evidence

    Generates:
    - problem_md: Markdown-formatted problem statement
    """
    tracker = get_tracker()
    tracker.start("frame_problem", "Generating problem statement")

    problem = _generate_output_problem_statement(state)
    problem_md = render_problem_statement_md(problem, state)
    debug_print(f"Problem statement generated ({len(problem_md)} chars)")

    tracker.complete("frame_problem", fields_updated=["problem_md"])
    return {"problem_md": problem_md}
