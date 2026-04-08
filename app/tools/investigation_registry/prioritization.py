"""Action prioritization logic based on sources and keywords."""

from app.tools.investigation_registry.actions import get_available_actions
from app.tools.investigation_registry.models import InvestigationAction
from app.types.evidence import EvidenceSource


def get_prioritized_actions(
    sources: list[EvidenceSource] | None = None,
    keywords: list[str] | None = None,
) -> list[InvestigationAction]:
    """Get actions prioritized by relevance to sources and keywords."""
    all_actions = get_available_actions()

    if not sources and not keywords:
        return all_actions

    scored_actions: list[tuple[InvestigationAction, int]] = []
    keywords_lower = [kw.lower() for kw in keywords] if keywords else []

    for action in all_actions:
        score = 0

        if sources and action.source in sources:
            score += 2

        if keywords_lower:
            use_cases_text = " ".join(action.use_cases).lower()
            matching_keywords = sum(1 for kw in keywords_lower if kw in use_cases_text)
            score += matching_keywords

        scored_actions.append((action, score))

    scored_actions.sort(key=lambda x: (-x[1], x[0].name))
    return [action for action, _ in scored_actions]
