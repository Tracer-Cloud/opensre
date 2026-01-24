"""Frame the problem and enrich context."""

from src.agent.state import InvestigationState

def node_frame_problem(state: InvestigationState) -> dict:  # noqa: ARG001
    """
    Enrich initial alert with context.

    Current MVP functionality:
    - Write a simple problem statement for the LLMs to use as input.

    Extend in the future to add:
    - Formulate investigation goals 
    - Service Graph lookup
    - Team/ownership enrichment
    """
    return {}

