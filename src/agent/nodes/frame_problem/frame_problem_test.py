from src.agent.nodes.frame_problem.frame_problem import (
    ProblemStatement,
    _render_problem_statement_md,
)
from src.agent.state import make_initial_state


def test_render_problem_statement_md_includes_alert_details() -> None:
    state = make_initial_state(
        alert_name="events_fact freshness SLA breached",
        affected_table="events_fact",
        severity="critical",
    )

    problem = ProblemStatement(
        summary="Freshness SLA breached",
        context="The table is stale and may impact downstream reporting.",
        investigation_goals=["Identify the failing pipeline step"],
        constraints=["Limited to available evidence sources"],
    )

    md = _render_problem_statement_md(problem, state)
    assert "# Problem Statement" in md
    assert "## Alert Details" in md
    assert "events_fact freshness SLA breached" in md
    assert "events_fact" in md
    assert "critical" in md
