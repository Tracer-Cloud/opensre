"""Tests for the MongoDB Atlas evidence mappers."""

from __future__ import annotations

from typing import Any

from integrations.mongodb_atlas.tools._evidence import (
    map_get_mongodb_atlas_alerts,
    map_get_mongodb_atlas_cluster_events,
    map_get_mongodb_atlas_cluster_metrics,
    map_get_mongodb_atlas_clusters,
    map_get_mongodb_atlas_performance_advisor,
)

# ---------------------------------------------------------------------------
# get_mongodb_atlas_clusters
# ---------------------------------------------------------------------------


def test_map_get_mongodb_atlas_clusters_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_clusters(
        evidence, {"available": True, "total_clusters": 3, "clusters": [{}] * 3}, {}
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_mongodb_atlas_clusters"
    assert entries[0]["summary"] == "3 cluster(s)"


def test_map_get_mongodb_atlas_clusters_skips_empty_and_unavailable() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_clusters(evidence, {"available": True, "total_clusters": 0}, {})
    assert "catalog_entries" not in evidence

    evidence2: dict[str, Any] = {}
    map_get_mongodb_atlas_clusters(evidence2, {"available": False, "error": "auth failed"}, {})
    assert "catalog_entries" not in evidence2


# ---------------------------------------------------------------------------
# get_mongodb_atlas_alerts
# ---------------------------------------------------------------------------


def test_map_get_mongodb_atlas_alerts_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_alerts(
        evidence, {"available": True, "total_alerts": 2, "alerts": [{}, {}]}, {}
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_mongodb_atlas_alerts"
    assert entries[0]["summary"] == "2 open alert(s)"


def test_map_get_mongodb_atlas_alerts_skips_empty() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_alerts(evidence, {"available": True, "total_alerts": 0}, {})
    assert "catalog_entries" not in evidence


# ---------------------------------------------------------------------------
# get_mongodb_atlas_cluster_events
# ---------------------------------------------------------------------------


def test_map_get_mongodb_atlas_cluster_events_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_cluster_events(
        evidence,
        {"available": True, "total_events": 4, "events": [{}] * 4},
        {"cluster_name": "prod-cluster"},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_mongodb_atlas_cluster_events"
    assert entries[0]["summary"] == "4 event(s) for cluster 'prod-cluster'"


def test_map_get_mongodb_atlas_cluster_events_skips_empty() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_cluster_events(evidence, {"available": True, "total_events": 0}, {})
    assert "catalog_entries" not in evidence


# ---------------------------------------------------------------------------
# get_mongodb_atlas_cluster_metrics
# ---------------------------------------------------------------------------


def test_map_get_mongodb_atlas_cluster_metrics_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_cluster_metrics(
        evidence,
        {
            "available": True,
            "measurements": {
                "CONNECTIONS": {"value": 42, "units": "SCALAR"},
                "SYSTEM_CPU_USER": {"value": 0.5, "units": "PERCENT"},
            },
        },
        {"cluster_name": "prod-cluster"},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_mongodb_atlas_cluster_metrics"
    assert entries[0]["summary"] == "2 metric(s) captured for cluster 'prod-cluster'"


def test_map_get_mongodb_atlas_cluster_metrics_skips_when_no_process_found() -> None:
    """Do not cite evidence when no cluster process is found."""
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_cluster_metrics(
        evidence,
        {"available": True, "note": "No processes found for cluster 'x'.", "measurements": {}},
        {},
    )
    assert "catalog_entries" not in evidence


# ---------------------------------------------------------------------------
# get_mongodb_atlas_performance_advisor
# ---------------------------------------------------------------------------


def test_map_get_mongodb_atlas_performance_advisor_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_performance_advisor(
        evidence,
        {
            "available": True,
            "total_suggested_indexes": 2,
            "suggested_indexes": [{}, {}],
            "total_slow_queries": 1,
            "slow_queries": [{}],
        },
        {"max_results": 50, "cluster_name": "prod-cluster"},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_mongodb_atlas_performance_advisor"
    assert entries[0]["summary"] == (
        "2 suggested index(es), 1 slow query event(s) for cluster 'prod-cluster'"
    )


def test_map_get_mongodb_atlas_performance_advisor_qualifies_when_page_is_saturated() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_performance_advisor(
        evidence,
        {
            "available": True,
            "total_suggested_indexes": 5,
            "suggested_indexes": [{}] * 5,
            "total_slow_queries": 5,
            "slow_queries": [{}] * 5,
        },
        {"max_results": 5},
    )
    assert evidence["catalog_entries"][0]["summary"].startswith(
        "5+ suggested index(es), 5+ slow query event(s)"
    )


def test_map_get_mongodb_atlas_performance_advisor_skips_when_both_empty() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_performance_advisor(
        evidence,
        {
            "available": True,
            "note": "No processes found.",
            "suggested_indexes": [],
            "slow_queries": [],
        },
        {},
    )
    assert "catalog_entries" not in evidence


def test_map_get_mongodb_atlas_performance_advisor_skips_unavailable() -> None:
    evidence: dict[str, Any] = {}
    map_get_mongodb_atlas_performance_advisor(evidence, {"available": False, "error": "x"}, {})
    assert "catalog_entries" not in evidence
