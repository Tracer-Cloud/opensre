"""Frame the problem and enrich context."""

from pydantic import BaseModel, Field

from src.agent.nodes.publish_findings.render import render_investigation_start
from src.agent.state import InvestigationState
from src.agent.tools.llm import get_llm


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


def _build_prompt(state: InvestigationState) -> str:
    """Build the prompt for generating a problem statement."""
    return f"""You are framing a data pipeline incident for investigation.

Alert Information:
- alert_name: {state.get("alert_name", "Unknown")}
- affected_table: {state.get("affected_table", "Unknown")}
- severity: {state.get("severity", "Unknown")}

Task:
Analyze the alert and provide a structured problem statement.
"""


def _render_problem_statement_md(
    problem: ProblemStatement, state: InvestigationState
) -> str:
    goals_md = "\n".join(f"- {goal}" for goal in problem.investigation_goals)
    constraints_md = "\n".join(f"- {constraint}" for constraint in problem.constraints)

    return f"""# Problem Statement

            ## Summary
            {problem.summary}

            ## Context
            {problem.context}

            ## Investigation Goals
            {goals_md}

            ## Constraints
            {constraints_md}

            ## Alert Details
            - **Alert**: {state.get("alert_name", "Unknown")}
            - **Table**: {state.get("affected_table", "Unknown")}
            - **Severity**: {state.get("severity", "Unknown")}

            ## Next Steps
            Proceed to gather evidence from relevant sources."""


def node_frame_problem(state: InvestigationState) -> dict:
    """
    Enrich the initial alert with investigation context using LLM.

    Uses Pydantic for output validation via structured output.
    Visible in LangSmith via LangChain tracing.

    Returns:
        dict with problem_md (str) containing the formatted problem statement
    """
    render_investigation_start(
        state.get("alert_name", "Unknown"),
        state.get("affected_table", "Unknown"),
        state.get("severity", "Unknown"),
    )

    prompt = _build_prompt(state)
    llm = get_llm()

    try:
        structured_llm = llm.with_structured_output(ProblemStatement)
        problem = structured_llm.invoke(prompt)
    except Exception:
        # Safe fallback if model fails to generate valid structure
        problem = ProblemStatement(
            summary="Investigation required for data pipeline alert",
            context="Alert triggered for affected table",
            investigation_goals=[
                "Identify root cause",
                "Assess impact",
                "Determine resolution",
            ],
            constraints=["Limited to available evidence sources"],
        )

    # ensure problem is not None (in case with_structured_output returns None on failure depending on config)
    if problem is None:
        problem = ProblemStatement(
            summary="Investigation required for data pipeline alert",
            context="Alert triggered for affected table",
            investigation_goals=[
                "Identify root cause",
                "Assess impact",
                "Determine resolution",
            ],
            constraints=["Limited to available evidence sources"],
        )

    return {"problem_md": _render_problem_statement_md(problem, state)}
