"""Tests for the healthy-short-circuit claim generation in diagnose_root_cause.

Covers the ``_handle_healthy_finding`` path: when ``is_clearly_healthy`` trips,
we must emit one validated claim per investigated evidence key that is present
in evidence — empty list values included, since an empty ``grafana_logs`` after
a completed investigation is itself a healthy signal. Non-investigation keys
like ``grafana_logs_query`` (a query string, not data) must not produce claims.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.nodes.root_cause_diagnosis import node as diag_node
from app.nodes.root_cause_diagnosis.evidence_checker import _INVESTIGATED_EVIDENCE_KEYS


def _run_handle_healthy_finding(evidence: dict) -> dict:
    """Invoke ``_handle_healthy_finding`` with a minimal state and fake tracker."""
    state = {"alert_name": "etl health check", "investigation_loop_count": 0}
    tracker = MagicMock()
    return diag_node._handle_healthy_finding(state, tracker, evidence)  # type: ignore[arg-type]


def _claim_keys(result: dict) -> list[str]:
    """Extract the leading evidence-key token from each validated claim."""
    claims = result["validated_claims"]
    return [c["claim"].split(" ", 1)[0] for c in claims]


class TestHealthyClaimsCoverage:
    """The healthy short-circuit output must cover the investigation keys that
    triggered it — no more, no less."""

    def test_empty_list_evidence_produces_a_claim(self) -> None:
        """An empty ``grafana_logs`` list is the healthy signal, not a reason to drop the claim."""
        result = _run_handle_healthy_finding({"grafana_logs": []})
        assert _claim_keys(result) == ["grafana_logs"]

    def test_claim_emitted_for_every_present_investigation_key(self) -> None:
        evidence = {
            "grafana_logs": [],
            "eks_pods": [{"name": "api-worker"}],
            "datadog_logs": [],
        }
        assert set(_claim_keys(_run_handle_healthy_finding(evidence))) == {
            "grafana_logs",
            "eks_pods",
            "datadog_logs",
        }

    def test_non_investigation_keys_do_not_produce_claims(self) -> None:
        """Query-string metadata like ``grafana_logs_query`` must not be reported as 'data'."""
        evidence = {
            "grafana_logs": [],
            "grafana_logs_query": 'severity:"error"',
            "datadog_logs_query": "status:error",
            "total_logs": 0,
        }
        assert _claim_keys(_run_handle_healthy_finding(evidence)) == ["grafana_logs"]

    def test_claim_order_is_deterministic(self) -> None:
        """Claim order must not depend on dict insertion order of ``evidence``."""
        e1 = {"eks_pods": [], "grafana_logs": []}
        e2 = {"grafana_logs": [], "eks_pods": []}
        assert _claim_keys(_run_handle_healthy_finding(e1)) == _claim_keys(
            _run_handle_healthy_finding(e2)
        )

    @pytest.mark.parametrize("key", sorted(_INVESTIGATED_EVIDENCE_KEYS))
    def test_every_investigation_key_is_recognised(self, key: str) -> None:
        """No investigation key should be silently dropped when empty."""
        assert _claim_keys(_run_handle_healthy_finding({key: []})) == [key]


class TestHealthyFindingShape:
    def test_returns_healthy_category_and_deterministic_fields(self) -> None:
        result = _run_handle_healthy_finding({"grafana_logs": []})
        assert result["root_cause_category"] == "healthy"
        assert result["validity_score"] == 1.0
        assert result["non_validated_claims"] == []
        assert result["remediation_steps"] == []
        assert "All monitored metrics are within normal bounds" in result["root_cause"]

    def test_preserves_investigation_loop_count(self) -> None:
        state = {"alert_name": "x", "investigation_loop_count": 7}
        tracker = MagicMock()
        result = diag_node._handle_healthy_finding(state, tracker, {"grafana_logs": []})  # type: ignore[arg-type]
        assert result["investigation_loop_count"] == 7

    def test_tracker_completion_recorded(self) -> None:
        tracker = MagicMock()
        diag_node._handle_healthy_finding(
            {"alert_name": "x", "investigation_loop_count": 0},
            tracker,
            {"grafana_logs": []},
        )  # type: ignore[arg-type]
        tracker.complete.assert_called_once()
        assert tracker.complete.call_args.kwargs["message"] == "healthy_short_circuit=true"


def test_diagnose_root_cause_short_circuits_through_healthy_finding(monkeypatch) -> None:
    """End-to-end: the diagnose entry point routes a clearly-healthy state through
    ``_handle_healthy_finding`` without invoking the LLM, and the resulting
    validated claims come from investigation keys, not query metadata."""
    monkeypatch.setenv("HEALTHY_SHORT_CIRCUIT", "true")

    state = {
        "alert_name": "synthetic health check",
        "raw_alert": {
            "state": "resolved",
            "commonLabels": {"severity": "info"},
            "commonAnnotations": {},
        },
        "evidence": {
            "grafana_logs": [],
            "grafana_logs_query": 'severity:"error"',
        },
        "context": {},
        "investigation_loop_count": 0,
    }

    with patch.object(diag_node, "get_llm_for_reasoning") as mock_llm_factory:
        result = diag_node.diagnose_root_cause(state)  # type: ignore[arg-type]
        mock_llm_factory.assert_not_called()

    assert result["root_cause_category"] == "healthy"
    claim_keys = [c["claim"].split(" ", 1)[0] for c in result["validated_claims"]]
    assert claim_keys == ["grafana_logs"]
