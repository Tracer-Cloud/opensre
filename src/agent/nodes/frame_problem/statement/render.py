"""Rendering helpers for the frame_problem statement node."""

from __future__ import annotations

from src.agent.nodes.frame_problem.statement.models import ProblemStatement
from src.agent.state import InvestigationState


def render_problem_statement_md(
    problem: ProblemStatement,
    state: InvestigationState,
) -> str:
    """Render the problem statement as Markdown."""
    goals_md = "\n".join(f"- {goal}" for goal in problem.investigation_goals)
    constraints_md = "\n".join(f"- {constraint}" for constraint in problem.constraints)

    # Get tracer run URL if available
    tracer_run_url = None
    evidence = state.get("evidence", {})
    tracer_web_run = evidence.get("tracer_web_run", {})
    if tracer_web_run.get("found") and tracer_web_run.get("run_url"):
        tracer_run_url = tracer_web_run.get("run_url")
    elif state.get("raw_alert") and isinstance(state.get("raw_alert"), dict):
        # Check if run_url is in raw_alert
        raw_alert = state.get("raw_alert", {})
        if raw_alert.get("run_url"):
            tracer_run_url = raw_alert.get("run_url")

    run_url_section = ""
    if tracer_run_url:
        run_url_section = f"\n**Tracer Pipeline Run**: [View Run]({tracer_run_url})\n"

    return f"""# Problem Statement
{run_url_section}
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
