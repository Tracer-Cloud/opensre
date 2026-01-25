"""Frame the problem statement.

This node generates a problem statement from extracted alert details and context.
It assumes extract_alert and build_context nodes have already run.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable

from src.agent.nodes.frame_problem.statement_node import node_frame_problem_statement
from src.agent.output import get_tracker
from src.agent.state import InvestigationState


def main(state: InvestigationState) -> dict:
    """
    Main entry point for framing the problem statement.

    Assumes:
    - extract_alert node has already populated alert_name, affected_table, severity, alert_json
    - build_context node has already populated evidence

    Generates:
    - problem_md: Markdown-formatted problem statement
    """
    tracker = get_tracker()
    tracker.start("frame_problem", "Generating problem statement")

    updates = node_frame_problem_statement(state)
    state = {**state, **updates}

    tracker.complete(
        "frame_problem",
        fields_updated=["problem_md"],
    )

    return {
        "problem_md": state.get("problem_md", ""),
    }


@traceable(name="node_frame_problem")
def node_frame_problem(state: InvestigationState) -> dict:
    """
    LangGraph node wrapper with LangSmith tracking.

    Kept for graph wiring; delegates to the main flow.
    """
    return main(state)


