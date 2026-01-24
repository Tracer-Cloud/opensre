"""Generate output reports."""

from langsmith import traceable

from src.agent.nodes.publish_findings.context import build_report_context
from src.agent.nodes.publish_findings.render import render_final_report
from src.agent.nodes.publish_findings.report import (
    format_problem_md,
    format_slack_message,
)
from src.agent.state import InvestigationState


def main(state: InvestigationState) -> dict:
    """
    Main entry point for publishing findings.

    Flow:
    1) Build report context from state
    2) Format Slack message and problem.md
    3) Render final report
    """
    ctx = build_report_context(state)
    slack_message = format_slack_message(ctx)
    problem_md = format_problem_md(ctx)
    render_final_report(slack_message)

    return {
        "slack_message": slack_message,
        "problem_md": problem_md,
    }


@traceable(name="node_publish_findings")
def node_publish_findings(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return main(state)
