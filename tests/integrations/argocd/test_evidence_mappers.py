"""Evidence mapper coverage for the argocd tools (#5563)."""

from __future__ import annotations

from typing import Any

from integrations.argocd.tools._evidence import (
    map_argocd_application_diff as _map_argocd_application_diff,
)
from integrations.argocd.tools._evidence import (
    map_argocd_application_status as _map_argocd_application_status,
)


class TestMapArgocdApplicationDiff:
    def test_records_entry_when_drift_detected(self) -> None:
        evidence: dict[str, Any] = {}

        _map_argocd_application_diff(
            evidence,
            {
                "available": True,
                "drift_detected": True,
                "diff_count": 3,
                "diffs": [{"kind": "Deployment"}, {"kind": "Service"}, {"kind": "ConfigMap"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "argocd_application_diff"
        assert "3" in entries[0]["summary"]

    def test_records_entry_when_modified_but_no_itemized_diffs(self) -> None:
        """Argo CD v3.3's {items, modified} response shape can report drift
        (modified=True) with an empty itemized diff list -- drift_detected is
        the authoritative signal, not diff_count, and a modified-but-
        unitemized result must still be cited, not silently dropped."""
        evidence: dict[str, Any] = {}

        _map_argocd_application_diff(
            evidence,
            {"available": True, "drift_detected": True, "diff_count": 0, "diffs": []},
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "argocd_application_diff"
        assert "drifted" in entries[0]["summary"]

    def test_records_nothing_when_no_drift(self) -> None:
        evidence: dict[str, Any] = {}

        _map_argocd_application_diff(
            evidence,
            {"available": True, "drift_detected": False, "diff_count": 0, "diffs": []},
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_argocd_application_diff(
            evidence,
            {
                "available": False,
                "error": "Argo CD integration is not configured.",
                "drift_detected": False,
                "diffs": [],
                "diff_count": 0,
            },
            {},
        )

        assert "catalog_entries" not in evidence


class TestMapArgocdApplicationStatus:
    def test_records_entry_for_single_application(self) -> None:
        evidence: dict[str, Any] = {}

        _map_argocd_application_status(
            evidence,
            {
                "available": True,
                "application": {
                    "name": "checkout-service",
                    "sync_status": "OutOfSync",
                    "health_status": "Degraded",
                },
                "recent_history": [],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "argocd_application_status"
        assert "checkout-service" in entries[0]["summary"]
        assert "OutOfSync" in entries[0]["summary"]
        assert "Degraded" in entries[0]["summary"]

    def test_records_entry_for_application_list(self) -> None:
        evidence: dict[str, Any] = {}

        _map_argocd_application_status(
            evidence,
            {"available": True, "applications": [{"name": "a"}, {"name": "b"}]},
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert "2" in entries[0]["summary"]

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_argocd_application_status(
            evidence,
            {
                "available": False,
                "error": "Argo CD integration is not configured.",
                "application": {},
                "applications": [],
                "recent_history": [],
            },
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_when_both_application_and_applications_empty(self) -> None:
        evidence: dict[str, Any] = {}

        _map_argocd_application_status(
            evidence,
            {"available": True, "application": {}, "applications": [], "recent_history": []},
            {},
        )

        assert "catalog_entries" not in evidence
