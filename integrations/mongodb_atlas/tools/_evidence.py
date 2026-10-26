"""Evidence mappers for the MongoDB Atlas investigation tools."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry


def map_get_mongodb_atlas_clusters(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the cluster count.

    ``total_clusters`` is the Atlas API's own ``totalCount`` -- a true
    server-side total unaffected by the client's page size -- so no "N+"
    qualifier is needed here, unlike the tools below whose counts are
    derived from a locally page-capped list.
    """
    if not output.get("available"):
        return
    total = output.get("total_clusters", 0)
    if not total:
        return
    record_evidence_entry(
        evidence,
        source="get_mongodb_atlas_clusters",
        label="MongoDB Atlas Clusters",
        summary=f"{total} cluster(s)",
    )


def map_get_mongodb_atlas_alerts(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the open alert count -- ``total_alerts`` is the API's real ``totalCount``."""
    if not output.get("available"):
        return
    total = output.get("total_alerts", 0)
    if not total:
        return
    record_evidence_entry(
        evidence,
        source="get_mongodb_atlas_alerts",
        label="MongoDB Atlas Alerts",
        summary=f"{total} open alert(s)",
    )


def map_get_mongodb_atlas_cluster_events(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the event count -- ``total_events`` is the API's real ``totalCount``."""
    if not output.get("available"):
        return
    total = output.get("total_events", 0)
    if not total:
        return
    summary = f"{total} event(s)"
    cluster_name = tool_input.get("cluster_name")
    if cluster_name:
        summary += f" for cluster '{cluster_name}'"
    record_evidence_entry(
        evidence,
        source="get_mongodb_atlas_cluster_events",
        label="MongoDB Atlas Cluster Events",
        summary=summary,
    )


def map_get_mongodb_atlas_cluster_metrics(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite how many process-level metrics were captured for the cluster."""
    if not output.get("available"):
        return
    measurements = output.get("measurements") or {}
    if not measurements:
        return
    summary = f"{len(measurements)} metric(s) captured"
    cluster_name = tool_input.get("cluster_name")
    if cluster_name:
        summary += f" for cluster '{cluster_name}'"
    record_evidence_entry(
        evidence,
        source="get_mongodb_atlas_cluster_metrics",
        label="MongoDB Atlas Cluster Metrics",
        summary=summary,
    )


def map_get_mongodb_atlas_performance_advisor(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite suggested-index and slow-query counts, qualifying page-capped totals.

    Unlike alerts/events/clusters, ``total_suggested_indexes``/
    ``total_slow_queries`` are just ``len(...)`` of a list the client itself
    capped via ``nIndexes``/``nLogs`` (``effective_limit``) -- no server-side
    total is echoed back, so a returned count at that ceiling may understate
    the true number -- use the "N+" convention against the caller's
    requested ``max_results``.
    """
    if not output.get("available"):
        return
    suggested = output.get("suggested_indexes") or []
    slow = output.get("slow_queries") or []
    if not suggested and not slow:
        return
    requested_limit = max(tool_input.get("max_results", 50), 1)
    total_suggested = output.get("total_suggested_indexes", len(suggested))
    total_slow = output.get("total_slow_queries", len(slow))
    suggested_label = (
        f"{total_suggested}+" if total_suggested >= requested_limit else str(total_suggested)
    )
    slow_label = f"{total_slow}+" if total_slow >= requested_limit else str(total_slow)
    summary = f"{suggested_label} suggested index(es), {slow_label} slow query event(s)"
    cluster_name = tool_input.get("cluster_name")
    if cluster_name:
        summary += f" for cluster '{cluster_name}'"
    record_evidence_entry(
        evidence,
        source="get_mongodb_atlas_performance_advisor",
        label="MongoDB Atlas Performance Advisor",
        summary=summary,
    )
