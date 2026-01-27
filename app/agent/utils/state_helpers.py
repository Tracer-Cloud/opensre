"""State helper utilities for working with InvestigationState."""

from app.agent.state import InvestigationState


def get_executed_sources(state: InvestigationState) -> set[str]:
    """
    Extract all executed sources from hypotheses history.

    This function consolidates the logic for extracting executed evidence sources
    from the state's executed_hypotheses history. It handles both the legacy
    single "source" field and the newer "sources" list field.

    Args:
        state: The investigation state containing executed_hypotheses

    Returns:
        A set of all evidence source names that have been executed

    Example:
        >>> state = {"executed_hypotheses": [
        ...     {"source": "tracer_web", "sources": ["tracer_web", "batch"]},
        ...     {"source": "cloudwatch"}
        ... ]}
        >>> get_executed_sources(state)
        {"tracer_web", "batch", "cloudwatch"}
    """
    executed_sources_set = set()
    for h in state.get("executed_hypotheses", []):
        # Handle newer "sources" list field
        sources = h.get("sources", [])
        if isinstance(sources, list):
            executed_sources_set.update(sources)

        # Handle legacy "source" field for backward compatibility
        single_source = h.get("source")
        if single_source:
            executed_sources_set.add(single_source)

    return executed_sources_set
