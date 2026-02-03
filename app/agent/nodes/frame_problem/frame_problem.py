"""Frame the problem statement.

This node generates a problem statement from extracted alert details and context.
It assumes extract_alert and build_context nodes have already run.
It updates state fields but does NOT render output directly.
"""

from typing import cast

from langsmith import traceable

from app.agent.memory import get_memory_context, is_memory_enabled
from app.agent.nodes.frame_problem.models import (
    ProblemStatement,
    ProblemStatementInput,
)
from app.agent.nodes.frame_problem.render import render_problem_statement_md
from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm


def _build_input_prompt(problem_input: ProblemStatementInput, memory_context: str = "") -> str:
    """Build the prompt for generating a problem statement."""
    memory_section = ""
    if memory_context:
        memory_section = f"""
Prior Problem Patterns (from memory):
{memory_context[:1000]}

Use these patterns as templates to frame the current problem quickly.
"""

    return f"""You are framing a data pipeline incident for investigation.

Alert Information:
- alert_name: {problem_input.alert_name}
- pipeline_name: {problem_input.pipeline_name}
- severity: {problem_input.severity}
{memory_section}
Task:
Analyze the alert and provide a structured problem statement.
"""


def _generate_output_problem_statement(
    state: InvestigationState, memory_context: str = ""
) -> ProblemStatement:
    """Use the LLM to generate a structured problem statement."""
    prompt = _build_input_prompt(ProblemStatementInput.from_state(state), memory_context)

    # Use fast model (Haiku) if memory provides guidance
    use_fast = bool(memory_context)
    llm = get_llm(use_fast_model=use_fast)

    try:
        structured_llm = llm.with_structured_output(ProblemStatement)
        chain = structured_llm.with_config(run_name="LLM – Draft problem statement")

        problem = chain.invoke(prompt)
    except Exception as err:
        debug_print(f"Problem statement LLM failed, using fallback: {err}")
        return _fallback_problem_statement(state)

    if problem is None:
        debug_print("Problem statement LLM returned no result, using fallback")
        return _fallback_problem_statement(state)

    return cast(ProblemStatement, problem)


def _fallback_problem_statement(state: InvestigationState) -> ProblemStatement:
    """Fallback problem statement when LLM is unavailable."""
    alert_name = state.get("alert_name", "Unknown")
    pipeline_name = state.get("pipeline_name", "Unknown")
    severity = state.get("severity", "Unknown")

    summary = f"{alert_name} detected on {pipeline_name} (severity: {severity})"
    context = "Automated fallback: LLM unavailable. Proceed with standard investigation playbook."
    investigation_goals = [
        "Identify failure point in pipeline execution",
        "Confirm impact on downstream outputs",
        "Gather minimal evidence to unblock RCA",
    ]
    constraints = ["Limited context due to LLM outage"]

    return ProblemStatement(
        summary=summary,
        context=context,
        investigation_goals=investigation_goals,
        constraints=constraints,
    )


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

    # Load memory context if enabled
    memory_context = ""
    if is_memory_enabled():
        from app.agent.memory.architecture_discovery import find_architecture_doc_for_pipeline

        pipeline_name = state.get("pipeline_name", "")
        seed_paths = find_architecture_doc_for_pipeline(pipeline_name)

        memory_context = get_memory_context(
            pipeline_name=pipeline_name,
            alert_id=state.get("alert_json", {}).get("alert_id"),
            seed_paths=seed_paths,
        )
        if memory_context:
            debug_print("[MEMORY] Loaded context for problem framing")

    problem = _generate_output_problem_statement(state, memory_context)
    problem_md = render_problem_statement_md(problem, state)
    debug_print(f"Problem statement generated ({len(problem_md)} chars)")

    tracker.complete("frame_problem", fields_updated=["problem_md"])
    return {"problem_md": problem_md}
