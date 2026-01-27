"""Build pre-incident context for the investigation."""

from langsmith import traceable

from app.agent.nodes.build_context.context_building import build_investigation_context
from app.agent.output import get_tracker
from app.agent.state import InvestigationState


@traceable(name="node_build_context")
def node_build_context(state: InvestigationState) -> dict:
    """Gather investigation context and store in context bucket."""
    tracker = get_tracker()
    tracker.start("build_context", "Building investigation context")

    context = build_investigation_context(state)

    tracker.complete("build_context", fields_updated=["context"])
    return {"context": context}
