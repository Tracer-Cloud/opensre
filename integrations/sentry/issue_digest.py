"""Compact Sentry issue digests for model context budgets."""

from __future__ import annotations

from typing import Any

from integrations.sentry.issue_clustering import (
    structural_cluster_key_for_issue,
    structural_cluster_label,
)
from integrations.sentry.issue_scoring import business_impact_score, classify_issue

_TOP_ISSUE_LIMIT = 5
_PRIORITY_CANDIDATE_LIMIT = 5
_CLUSTER_SHORT_ID_LIMIT = 3
_DEFAULT_PAGE_LIMIT = 100

_STATS_PERIOD_LABELS: dict[str, str] = {
    "24h": "last 24 hours",
    "7d": "last 7 days",
    "14d": "last 14 days",
    "30d": "last 30 days",
}


def stats_period_label(stats_period: str) -> str:
    period = stats_period.strip() or "24h"
    return _STATS_PERIOD_LABELS.get(period, f"last {period}")


def scope_summary_for_digest(
    *,
    issue_count: int,
    stats_period: str,
    query: str,
    page_limit: int = _DEFAULT_PAGE_LIMIT,
) -> str:
    scope = build_scope_metadata(
        issue_count=issue_count,
        stats_period=stats_period,
        query=query,
        page_limit=page_limit,
    )
    summary = scope["scope_summary"]
    return str(summary)


def build_scope_metadata(
    *,
    issue_count: int,
    stats_period: str,
    query: str,
    page_limit: int = _DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Describe how complete the returned issue page is for model-facing summaries."""
    period_label = stats_period_label(stats_period)
    query_label = query.strip() or "is:unresolved"
    saturated = page_limit > 0 and issue_count >= page_limit

    if saturated:
        count_label = f"{page_limit}+"
        completeness = "partial_page"
        scope_summary = (
            f"{count_label} unresolved issue groups in the {period_label} matching "
            f"{query_label} (first page of {page_limit}; more may exist in this window)"
        )
        scope_note = (
            f"Returned the first {page_limit} issue groups from Sentry for "
            f"{query_label} in the {period_label}; additional unresolved groups "
            f"may exist in the same window. Cluster percentages are shares of "
            f"this {page_limit}-issue page, not of all unresolved issues in the org."
        )
    elif issue_count == 0:
        count_label = "0"
        completeness = "empty"
        scope_summary = f"No unresolved issue groups in the {period_label} matching {query_label}"
        scope_note = (
            f"No issue groups matched {query_label} in the {period_label} "
            f"(complete — nothing returned). Do not widen the search window "
            f"silently; a broader lookback must be a separate, clearly labeled "
            f"follow-up."
        )
    else:
        count_label = str(issue_count)
        completeness = "complete_page"
        plural = "s" if issue_count != 1 else ""
        scope_summary = (
            f"{count_label} unresolved issue group{plural} in the {period_label} "
            f"matching {query_label} (complete — all matching groups in this window)"
        )
        scope_note = (
            f"Returned all {issue_count} issue group{plural} matching {query_label} "
            f"in the {period_label} (under the {page_limit}-issue page cap). Cluster "
            f"percentages are shares of this complete result set."
        )

    return {
        "issue_count": issue_count,
        "issue_count_label": count_label,
        "has_results": issue_count > 0,
        "page_limit": page_limit,
        "page_saturated": saturated,
        "page_complete": not saturated and issue_count > 0,
        "completeness": completeness,
        "scope_summary": scope_summary,
        "scope_note": scope_note,
        "percent_basis": "returned_page",
    }


def slim_issue(
    issue: dict[str, Any],
    *,
    structural_cluster: str,
    classification: str,
    impact_score: int,
    impact_reasons: list[str],
) -> dict[str, Any]:
    return {
        "id": issue.get("id"),
        "short_id": issue.get("shortId") or issue.get("id"),
        "title": issue.get("title"),
        "culprit": issue.get("culprit"),
        "structural_cluster": structural_cluster,
        "structural_label": structural_cluster_label(structural_cluster),
        "classification": classification,
        "count": issue.get("count"),
        "user_count": issue.get("userCount"),
        "first_seen": issue.get("firstSeen"),
        "last_seen": issue.get("lastSeen"),
        "level": issue.get("level"),
        "status": issue.get("status"),
        "business_impact_score": impact_score,
        "impact_reasons": impact_reasons,
    }


def build_sentry_issue_digest(
    issues: list[dict[str, Any]],
    *,
    stats_period: str,
    query: str,
    page_limit: int = _DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Build a bounded digest from the full issue page for model-facing summaries."""
    issue_count = len(issues)
    scope = build_scope_metadata(
        issue_count=issue_count,
        stats_period=stats_period,
        query=query,
        page_limit=page_limit,
    )
    cluster_counts: dict[str, int] = {}
    cluster_titles: dict[str, list[str]] = {}
    cluster_short_ids: dict[str, list[str]] = {}
    enriched: list[tuple[int, dict[str, Any]]] = []

    for issue in issues:
        structural_cluster = structural_cluster_key_for_issue(issue)
        cluster_counts[structural_cluster] = cluster_counts.get(structural_cluster, 0) + 1
        title = str(issue.get("title") or "").strip()
        if title:
            cluster_titles.setdefault(structural_cluster, []).append(title)
        short_id = str(issue.get("shortId") or issue.get("id") or "").strip()
        if short_id:
            cluster_short_ids.setdefault(structural_cluster, []).append(short_id)
        classification = classify_issue(issue)
        impact_score, impact_reasons = business_impact_score(issue)
        enriched.append(
            (
                impact_score,
                slim_issue(
                    issue,
                    structural_cluster=structural_cluster,
                    classification=classification,
                    impact_score=impact_score,
                    impact_reasons=impact_reasons,
                ),
            )
        )

    structural_clusters = []
    for key, count in sorted(cluster_counts.items(), key=lambda item: item[1], reverse=True):
        titles = tuple(cluster_titles.get(key, ()))
        top_titles = tuple(
            title
            for title, _ in sorted(
                ((title, titles.count(title)) for title in set(titles)),
                key=lambda item: item[1],
                reverse=True,
            )[:2]
        )
        structural_clusters.append(
            {
                "key": key,
                "label": structural_cluster_label(key, sample_titles=top_titles),
                "issue_count": count,
                "percent": round((count / issue_count) * 100) if issue_count else 0,
                "sample_titles": list(top_titles),
                "sample_short_ids": list(dict.fromkeys(cluster_short_ids.get(key, ())))[
                    :_CLUSTER_SHORT_ID_LIMIT
                ],
            }
        )

    ranked_issues = [issue for _, issue in sorted(enriched, key=lambda item: item[0], reverse=True)]
    top_issues = ranked_issues[:_TOP_ISSUE_LIMIT]
    priority_candidates = [
        {
            "short_id": issue["short_id"],
            "title": issue.get("title"),
            "structural_cluster": issue.get("structural_cluster"),
            "business_impact_score": issue.get("business_impact_score"),
            "impact_reasons": issue.get("impact_reasons"),
            "count": issue.get("count"),
            "user_count": issue.get("user_count"),
        }
        for issue in ranked_issues[:_PRIORITY_CANDIDATE_LIMIT]
    ]
    priority_issue = ranked_issues[0] if ranked_issues else None

    return {
        "issue_count": issue_count,
        "issue_count_label": scope["issue_count_label"],
        "has_results": scope["has_results"],
        "page_limit": scope["page_limit"],
        "page_saturated": scope["page_saturated"],
        "page_complete": scope["page_complete"],
        "completeness": scope["completeness"],
        "stats_period": stats_period,
        "stats_period_label": stats_period_label(stats_period),
        "query": query,
        "scope_summary": scope["scope_summary"],
        "scope_note": scope["scope_note"],
        "percent_basis": scope["percent_basis"],
        "structural_clusters": structural_clusters,
        "top_issues": top_issues,
        "priority_candidates": priority_candidates,
        "priority_issue_id": priority_issue.get("id") if priority_issue else None,
        "priority_short_id": priority_issue.get("short_id") if priority_issue else None,
        "priority_impact_reasons": (priority_issue.get("impact_reasons") if priority_issue else []),
    }


__all__ = [
    "build_sentry_issue_digest",
    "build_scope_metadata",
    "business_impact_score",
    "classify_issue",
    "scope_summary_for_digest",
    "slim_issue",
    "stats_period_label",
    "structural_cluster_key_for_issue",
    "structural_cluster_label",
]
