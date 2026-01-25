"""Frame the problem and enrich context.

This node extracts alert details, builds context, and generates a problem statement.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable

from src.agent.nodes.frame_problem.context.context_node import node_frame_problem_context
from src.agent.nodes.frame_problem.extract.extract_node import node_frame_problem_extract
from src.agent.nodes.frame_problem.statement.statement_node import node_frame_problem_statement
from src.agent.output import get_tracker
from src.agent.state import InvestigationState


def main(state: InvestigationState) -> dict:
    """
    Main entry point for framing the problem.

    This keeps the core flow easy to follow:
    1) Extract alert fields from raw input using the LLM
    2) Show the investigation header
    3) Generate a structured problem statement
    4) Return parsed alert JSON for downstream nodes
    """
    tracker = get_tracker()
    tracker.start("frame_problem", "Framing problem via sub-nodes")

    updates = node_frame_problem_extract(state)
    state = {**state, **updates}

    updates = node_frame_problem_context(state)
    state = {**state, **updates}

    updates = node_frame_problem_statement(state)
    state = {**state, **updates}

    tracker.complete(
        "frame_problem",
        fields_updated=["alert_name", "affected_table", "severity", "evidence", "problem_md"],
    )

    return {
        "alert_name": state.get("alert_name", ""),
        "affected_table": state.get("affected_table", ""),
        "severity": state.get("severity", ""),
        "alert_json": state.get("alert_json", {}),
        "problem_md": state.get("problem_md", ""),
        "evidence": state.get("evidence", {}),
    }


@traceable(name="node_frame_problem")
def node_frame_problem(state: InvestigationState) -> dict:
    """
    LangGraph node wrapper with LangSmith tracking.

    Kept for graph wiring; delegates to the main flow.
    """
    return main(state)


