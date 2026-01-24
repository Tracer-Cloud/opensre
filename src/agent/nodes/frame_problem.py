"""Frame the problem and enrich context."""

from src.agent.state import InvestigationState


def node_frame_problem(state: InvestigationState) -> dict:  # noqa: ARG001
    """
    Enrich initial alert with context.

    Currently a pass-through - extend to add:
    - Historical incident lookup
    - Related alerts correlation
    - Team/ownership enrichment
    """
    return {}

