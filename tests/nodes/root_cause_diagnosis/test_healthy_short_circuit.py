"""Tests for the healthy-short-circuit claim generation in diagnose_root_cause.

Covers the ``_handle_healthy_finding`` path: when ``is_clearly_healthy`` trips,
we must emit one validated claim per evidence source that was either
investigated (present in ``INVESTIGATED_EVIDENCE_KEYS`` — empty list counts,
since an empty ``grafana_logs`` after a completed investigation is itself a
healthy signal) or carries non-metadata data content. Metadata entries such
as ``grafana_logs_query`` (a query string), ``eks_total_pods`` (a count), or
``datadog_fetch_ms`` (a timing) must not produce claims.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.nodes.root_cause_diagnosis import node as diag_node
from app.nodes.root_cause_diagnosis.evidence_checker import INVESTIGATED_EVIDENCE_KEYS


def _run_handle_healthy_finding(evidence: dict) -> dict:
    """Invoke ``_handle_healthy_finding`` with a minimal state and fake tracker."""
    state = {"alert_name": "etl health check", "investigation_loop_count": 0}
    tracker = MagicMock()
    return diag_node._handle_healthy_finding(state, tracker, evidence)  # type: ignore[arg-type]


def _claim_keys(result: dict) -> list[str]:
    """Extract the leading evidence-key token from each validated claim."""
    claims = result["validated_claims"]
    return [c["claim"].split(" ", 1)[0] for c in claims]


class TestInvestigationKeyClaims:
    """Investigation keys must always produce a claim when present — empty
    values included, because an empty list is the healthy signal that
    triggered the short-circuit."""

    def test_empty_list_investigation_key_produces_a_claim(self) -> None:
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

    @pytest.mark.parametrize("key", sorted(INVESTIGATED_EVIDENCE_KEYS))
    def test_every_investigation_key_is_recognised(self, key: str) -> None:
        """No investigation key should be silently dropped when empty."""
        assert _claim_keys(_run_handle_healthy_finding({key: []})) == [key]


class TestMetadataKeyFiltering:
    """Query strings, counts, totals, timings, and resource-name metadata keys
    produce incoherent findings (\"X query data confirmed\") and must never
    appear in the claim list — even when the adjacent investigation key is
    also present."""

    @pytest.mark.parametrize(
        "metadata_key, value",
        [
            ("grafana_logs_query", 'severity:"error"'),
            ("datadog_logs_query", "status:error"),
            ("datadog_monitors_count", 2),
            ("datadog_events_count", 5),
            ("eks_total_pods", 3),
            ("eks_total_deployments", 1),
            ("eks_total_warning_count", 0),
            ("eks_total_nodes", 2),
            ("eks_not_ready_count", 0),
            ("datadog_fetch_ms", {"logs": 42}),
            ("datadog_pod_name", "payments-api-xkp2q"),
            ("datadog_container_name", "payments-api"),
            ("datadog_kube_namespace", "payments"),
            ("betterstack_source", "heroku"),
            ("grafana_log_count", 11),
            ("grafana_trace_count", 20),
            ("honeycomb_trace_count", 5),
            ("alertmanager_alerts_total", 3),
            ("alertmanager_silences_total", 1),
            ("total_logs", 100),
            ("total_failed_jobs_count", 0),
            ("cloudwatch_logs_count", 5),
        ],
    )
    def test_metadata_key_in_isolation_produces_no_claim(
        self, metadata_key: str, value: object
    ) -> None:
        assert _claim_keys(_run_handle_healthy_finding({metadata_key: value})) == []

    def test_metadata_keys_alongside_investigation_keys_are_filtered(self) -> None:
        evidence = {
            "grafana_logs": [],
            "grafana_logs_query": 'severity:"error"',
            "grafana_log_count": 0,
            "datadog_logs": [],
            "datadog_logs_query": "status:error",
            "datadog_monitors_count": 2,
            "datadog_fetch_ms": {"logs": 42},
            "eks_pods": [{"name": "x"}],
            "eks_total_pods": 3,
            "eks_total_deployments": 1,
        }
        assert _claim_keys(_run_handle_healthy_finding(evidence)) == [
            "datadog_logs",
            "eks_pods",
            "grafana_logs",
        ]


class TestTruthyNonInvestigationDataClaims:
    """Truthy evidence keys that are not investigation-keys but also not
    metadata represent real gathered data (e.g. ``datadog_events``,
    ``datadog_error_logs``, ``grafana_traces``, ``honeycomb_traces``,
    ``eks_failing_pods``) and should still produce a claim — matching the
    pre-fix observable behavior on those keys."""

    @pytest.mark.parametrize(
        "key, value",
        [
            ("datadog_events", [{"id": "e1"}]),
            ("datadog_error_logs", [{"message": "err"}]),
            ("grafana_traces", [{"trace_id": "t1"}]),
            ("grafana_error_logs", [{"line": "err"}]),
            ("grafana_service_names", ["api", "db"]),
            ("honeycomb_traces", [{"trace_id": "h1"}]),
            ("coralogix_logs", [{"text": "info"}]),
            ("coralogix_error_logs", [{"text": "err"}]),
            ("alertmanager_alerts", [{"status": "resolved"}]),
            ("alertmanager_silences", [{"id": "s1"}]),
            ("eks_failing_pods", [{"name": "p"}]),
            ("eks_high_restart_pods", [{"name": "p", "restarts": 3}]),
            ("eks_degraded_deployments", [{"name": "d"}]),
            ("failed_tools", [{"name": "query_x"}]),
            ("error_logs", [{"message": "err"}]),
            ("host_metrics", {"data": {"cpu": 42}}),
            ("s3_object", {"found": True}),
            ("lambda_logs", [{"line": "info"}]),
            ("lambda_function", {"name": "fn"}),
        ],
    )
    def test_truthy_non_metadata_key_produces_claim(self, key: str, value: object) -> None:
        assert _claim_keys(_run_handle_healthy_finding({key: value})) == [key]

    def test_empty_non_investigation_key_does_not_produce_claim(self) -> None:
        """Unlike investigation keys, empty non-investigation keys don't count."""
        assert _claim_keys(_run_handle_healthy_finding({"datadog_events": []})) == []

    def test_random_custom_truthy_key_is_claimed(self) -> None:
        """Forward-compat: new data keys from future mappers produce claims
        without a code change, as long as they aren't metadata-shaped."""
        assert _claim_keys(_run_handle_healthy_finding({"my_new_source_data": [1]})) == [
            "my_new_source_data"
        ]


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

    def test_claim_order_is_deterministic(self) -> None:
        """Claim order must not depend on dict insertion order of ``evidence``."""
        e1 = {"eks_pods": [], "grafana_logs": [], "datadog_events": [{"id": "e1"}]}
        e2 = {"datadog_events": [{"id": "e1"}], "grafana_logs": [], "eks_pods": []}
        assert _claim_keys(_run_handle_healthy_finding(e1)) == _claim_keys(
            _run_handle_healthy_finding(e2)
        )


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
            "grafana_log_count": 0,
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
