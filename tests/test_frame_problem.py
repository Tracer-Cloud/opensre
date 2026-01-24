import json

from src.agent.nodes.frame_problem.frame_problem import (
    _extract_json_object,
    _parse_problem_statement_json,
    _render_problem_statement_md,
)
from src.agent.state import make_initial_state


def test_extract_json_object_handles_wrapped_output() -> None:
    payload = {
        "summary": "Freshness SLA breached",
        "context": "The warehouse table has not been updated within SLA.",
        "investigation_goals": ["Find the failing upstream job"],
        "constraints": ["Use only available evidence sources"],
    }
    response = f"Here you go:\n```json\n{json.dumps(payload, indent=2)}\n```\n"

    extracted = _extract_json_object(response)
    assert json.loads(extracted)["summary"] == "Freshness SLA breached"


def test_parse_problem_statement_json_validates_schema() -> None:
    response = json.dumps(
        {
            "summary": "Pipeline delay",
            "context": "A downstream table is stale due to upstream delays.",
            "investigation_goals": ["Check latest pipeline run", "Check S3 markers"],
            "constraints": ["No manual backfills during business hours"],
        }
    )

    problem = _parse_problem_statement_json(response)
    assert problem.summary == "Pipeline delay"
    assert len(problem.investigation_goals) == 2


def test_parse_problem_statement_json_falls_back_on_invalid() -> None:
    problem = _parse_problem_statement_json("not json")
    assert problem.summary
    assert problem.investigation_goals
    assert problem.constraints


def test_render_problem_statement_md_includes_alert_details() -> None:
    state = make_initial_state(
        alert_name="events_fact freshness SLA breached",
        affected_table="events_fact",
        severity="critical",
    )

    problem = _parse_problem_statement_json(
        json.dumps(
            {
                "summary": "Freshness SLA breached",
                "context": "The table is stale and may impact downstream reporting.",
                "investigation_goals": ["Identify the failing pipeline step"],
                "constraints": ["Limited to available evidence sources"],
            }
        )
    )

    md = _render_problem_statement_md(problem, state)
    assert "# Problem Statement" in md
    assert "## Alert Details" in md
    assert "events_fact freshness SLA breached" in md
    assert "events_fact" in md
    assert "critical" in md

