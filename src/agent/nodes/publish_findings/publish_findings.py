"""Generate output reports.

This node ONLY sets state fields. It does NOT render output.
Rendering is handled by the demo runner / CLI using src.agent.output.
"""

from langsmith import traceable

from src.agent.nodes.publish_findings.context import build_report_context
from src.agent.nodes.publish_findings.report import (
    format_slack_message,
)
from src.agent.state import InvestigationState


def main(state: InvestigationState) -> dict:
    """
    Main entry point for publishing findings.

    Flow:
    1) Build report context from state
    2) Format Slack message and problem.md
    3) Return state fields (NO rendering here)
    """
    ctx = build_report_context(state)
    slack_message = format_slack_message(ctx)

    # Only set state fields - rendering happens in the runner
    return {
        "slack_message": slack_message,
    }


@traceable(name="node_publish_findings")
def node_publish_findings(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return main(state)
