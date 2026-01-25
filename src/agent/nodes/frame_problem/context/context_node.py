"""Build pre-incident context for the investigation."""

from langsmith import traceable

from src.agent.nodes.frame_problem.context.context_building import build_investigation_context
from src.agent.output import get_tracker
from src.agent.state import InvestigationState


@traceable(name="node_frame_problem_context")
def node_frame_problem_context(state: InvestigationState) -> dict:
    """Gather investigation context and merge into evidence."""
    tracker = get_tracker()
    tracker.start("frame_problem_context", "Building investigation context")

    context = build_investigation_context(state)
    evidence = {
        **state.get("evidence", {}),
        **context,
    }

    tracker.complete("frame_problem_context", fields_updated=["evidence"])
    return {"evidence": evidence}
