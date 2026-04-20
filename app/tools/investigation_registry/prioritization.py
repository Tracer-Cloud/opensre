"""Action prioritization logic based on sources and keywords."""

from __future__ import annotations

from typing import Any

from app.tools.investigation_registry.actions import get_available_actions
from app.tools.investigation_registry.models import InvestigationAction
from app.types.evidence import EvidenceSource

# Deterministic fallback set when no tools match detected sources.
# These are low-cost, always-safe tools that provide broad coverage.
FALLBACK_TOOLS: tuple[str, ...] = ("get_sre_guidance",)
DETERMINISTIC_FALLBACK_REASON = "included as deterministic fallback (no high-confidence match)"


def get_prioritized_actions(
    sources: list[EvidenceSource] | None = None,
    keywords: list[str] | None = None,
) -> list[InvestigationAction]:
    """Get actions prioritized by relevance to sources and keywords."""
    actions, _ = get_prioritized_actions_with_reasons(sources, keywords)
    return actions


def get_prioritized_actions_with_reasons(
    sources: list[EvidenceSource] | None = None,
    keywords: list[str] | None = None,
) -> tuple[list[InvestigationAction], list[dict[str, Any]]]:
    """Get actions prioritized by relevance, with human-readable inclusion reasons.

    Returns:
        Tuple of (prioritized_actions, inclusion_reasons) where each reason is a dict
        with keys: name, score, reasons (list of strings), source, tags.
    """
    all_actions = get_available_actions()

    if not sources and not keywords:
        reasons = [
            {
                "name": a.name,
                "score": 0,
                "reasons": ["no source/keyword filters applied"],
                "source": a.source,
                "tags": list(a.tags),
            }
            for a in all_actions
        ]
        return all_actions, reasons

    scored: list[tuple[InvestigationAction, int, list[str]]] = []
    keywords_lower = [kw.lower() for kw in keywords] if keywords else []

    for action in all_actions:
        score = 0
        action_reasons: list[str] = []

        if sources and action.source in sources:
            score += 2
            action_reasons.append(f"source '{action.source}' matches detected sources")

        if keywords_lower:
            use_cases_text = " ".join(action.use_cases).lower()
            matched = [kw for kw in keywords_lower if kw in use_cases_text]
            if matched:
                score += len(matched)
                action_reasons.append(f"keywords matched: {', '.join(matched)}")

        if not action_reasons:
            action_reasons.append("no source or keyword match")

        scored.append((action, score, action_reasons))

    scored.sort(key=lambda x: (-x[1], x[0].name))

    # Deterministic fallback: if no tool scored above 0, ensure the fallback set
    # is included so the investigation has at least one safe tool to call.
    top_score = scored[0][1] if scored else 0
    if top_score == 0:
        for action, _score, action_reasons in scored:
            if action.name in FALLBACK_TOOLS:
                action_reasons.append(DETERMINISTIC_FALLBACK_REASON)

    actions = [action for action, _, _ in scored]
    reasons = [
        {
            "name": action.name,
            "score": score,
            "reasons": action_reasons,
            "source": action.source,
            "tags": list(action.tags),
        }
        for action, score, action_reasons in scored
    ]
    return actions, reasons
