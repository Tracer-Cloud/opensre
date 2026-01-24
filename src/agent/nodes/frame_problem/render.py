"""Rendering helpers for the frame_problem node."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agent.state import InvestigationState

if TYPE_CHECKING:
    from src.agent.nodes.frame_problem.frame_problem import ProblemStatement


def render_problem_statement_md(
    problem: ProblemStatement,
    state: InvestigationState,
) -> str:
    """Render the problem statement as Markdown."""
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

            ## Available Evidence Sources
            - **Tracer pipeline**: run status, tasks, metrics, logs
            - **S3 storage**: output files and _SUCCESS marker
            - **AWS Batch**: job status and failure reasons

            ## Next Steps
            Proceed to gather evidence from relevant sources."""
