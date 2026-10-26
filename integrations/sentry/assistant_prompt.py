"""Sentry conversational-assistant prompt fragment — Sentry summary formatting rule.

Registered with :func:`infrastructure.harness_providers.register_assistant_prompt_fragment`
from ``integrations/harness_adapters.py``.
"""

from __future__ import annotations


def sentry_assistant_prompt_fragment() -> str:
    return (
        "Sentry summary: open **I found:** with digest.scope_summary verbatim, then "
        "add digest.scope_note on the next line when page_saturated is true or when "
        "clarifying completeness matters. When digest.completeness is empty, say the "
        "requested window had no unresolved groups — do not summarize issues from a "
        "different window or imply activity outside that period. Use "
        "digest.structural_clusters for themed buckets — format each as "
        "`N issues (P%)` using issue_count and percent (percent_basis is "
        "returned_page). Include sample_short_ids when present. Never show a bare "
        "project slug without explaining samples. Priority table: rank clusters from "
        "priority_candidates and business_impact_score / impact_reasons; include "
        "Sample IDs column. Penalize high-count zero-user retry noise. Do not ask to "
        "narrow or repeat search; offer a separately labeled broader window only if "
        "the user asks."
    )


__all__ = ["sentry_assistant_prompt_fragment"]
