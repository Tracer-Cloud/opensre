"""Tests for the healthy-short-circuit claim generation in diagnose_root_cause.

Covers ``_handle_healthy_finding``: when ``is_clearly_healthy`` trips, we
must emit one validated claim per evidence source present in the explicit
``CLAIM_EVIDENCE_KEYS`` whitelist. Investigation keys from
``INVESTIGATED_EVIDENCE_KEYS`` always qualify (empty lists included — an
empty ``grafana_logs`` after a completed investigation is the healthy
signal). Other whitelisted data keys qualify only when non-empty. Everything
else — query strings, counts, timings, resource names, trace IDs — is
ignored even when truthy.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.nodes.root_cause_diagnosis import node as diag_node
from app.nodes.root_cause_diagnosis.evidence_checker import (
    CLAIM_EVIDENCE_KEYS,
    INVESTIGATED_EVIDENCE_KEYS,
)


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


class TestAdjacentDataKeys:
    """Non-investigation whitelist entries produce claims only when non-empty."""

    _ADJACENT = sorted(CLAIM_EVIDENCE_KEYS - INVESTIGATED_EVIDENCE_KEYS)

    @pytest.mark.parametrize("key", _ADJACENT)
    def test_truthy_adjacent_key_produces_claim(self, key: str) -> None:
        # A non-empty list value is enough to trigger the truthiness gate.
        assert _claim_keys(_run_handle_healthy_finding({key: [{"stub": 1}]})) == [key]

    @pytest.mark.parametrize("key", _ADJACENT)
    def test_empty_adjacent_key_produces_no_claim(self, key: str) -> None:
        # Unlike investigation keys, empty adjacent keys are not claimed —
        # they signal the tool did not return data, not that the system is healthy.
        assert _claim_keys(_run_handle_healthy_finding({key: []})) == []


class TestNonWhitelistedKeysFiltered:
    """Every metadata shape that the mappers emit alongside primary data keys
    must be filtered out — query strings, counts, totals, timings, resource
    names, trace IDs, source URLs, limits, service names, windows, etc."""

    # Exhaustive set of metadata / non-data keys enumerated by auditing every
    # mapper in app/nodes/investigate/processing/post_process.py.
    _METADATA_KEYS: tuple[tuple[str, object], ...] = (
        # Counts
        ("total_jobs", 3),
        ("total_tools", 5),
        ("total_logs", 42),
        ("cloudwatch_event_count", 12),
        ("lambda_invocation_count", 7),
        ("lambda_error_count", 2),
        ("grafana_alert_rules_count", 4),
        ("datadog_monitors_count", 8),
        ("datadog_events_count", 11),
        ("honeycomb_trace_count", 6),
        ("coralogix_logs_count", 15),
        ("betterstack_logs_count", 22),
        ("git_deploy_timeline_count", 9),
        ("eks_total_warning_count", 0),
        ("eks_not_ready_count", 0),
        ("s3_object_count", 14),
        # Totals / aggregates
        ("alertmanager_alerts_total", 4),
        ("alertmanager_silences_total", 1),
        ("vercel_deployments_total", 5),
        ("vercel_total_events", 20),
        ("vercel_total_runtime_logs", 33),
        ("eks_total_pods", 3),
        ("eks_total_deployments", 1),
        ("eks_total_nodes", 2),
        # Query strings
        ("grafana_logs_query", 'severity:"error"'),
        ("datadog_logs_query", "status:error"),
        ("coralogix_logs_query", 'service:"api"'),
        ("github_code_query", "repo:x query:y"),
        # Timings
        ("datadog_fetch_ms", {"logs": 42}),
        # Service names / identifiers / URLs / text summaries / misc
        ("grafana_logs_service", "api"),
        ("grafana_traces_service", "api"),
        ("grafana_metric_name", "CPUUtilization"),
        ("grafana_metrics_service", "api"),
        ("datadog_pod_name", "payments-api-x"),
        ("datadog_container_name", "payments-api"),
        ("datadog_kube_namespace", "payments"),
        ("honeycomb_dataset", "api"),
        ("honeycomb_service_name", "api"),
        ("honeycomb_trace_id", "abc123"),
        ("honeycomb_query_url", "https://ui.honeycomb.io/..."),
        ("coralogix_application_name", "payments"),
        ("coralogix_subsystem_name", "api"),
        ("coralogix_trace_id", "xyz789"),
        ("betterstack_source", "heroku"),
        ("betterstack_logs_limit", 1000),
        ("vercel_project_id", "prj_123"),
        ("vercel_deployment_id", "dep_456"),
        ("github_code_text", "summary text"),
        ("github_file_text", "file text"),
        ("github_commits_text", "commits text"),
        ("git_deploy_timeline_window", {"from": "t0", "to": "t1"}),
        ("eks_pod_logs_pod_name", "payments-api-x"),
        ("eks_pod_logs_namespace", "payments"),
        # Summary-of-sample fields
        (
            "cloudwatch_latest_error",
            "OutOfMemory",
        ),  # intentionally: claimed via the whitelist entry above, not here
    )

    @pytest.mark.parametrize(
        "metadata_key, value",
        # Filter out any key that is actually claim-worthy so the test stays
        # honest — cloudwatch_latest_error is both listed above (as a
        # reminder) and in the whitelist; drop it from the metadata sweep.
        [(k, v) for (k, v) in _METADATA_KEYS if k not in CLAIM_EVIDENCE_KEYS],
    )
    def test_metadata_key_in_isolation_produces_no_claim(
        self, metadata_key: str, value: object
    ) -> None:
        assert _claim_keys(_run_handle_healthy_finding({metadata_key: value})) == []

    def test_metadata_keys_alongside_investigation_keys_are_filtered(self) -> None:
        evidence = {
            "grafana_logs": [],
            "grafana_logs_query": 'severity:"error"',
            "grafana_logs_service": "api",
            "datadog_logs": [],
            "datadog_logs_query": "status:error",
            "datadog_monitors_count": 2,
            "datadog_fetch_ms": {"logs": 42},
            "datadog_pod_name": "payments-api-x",
            "eks_pods": [{"name": "x"}],
            "eks_total_pods": 3,
            "eks_total_deployments": 1,
            "honeycomb_trace_id": "abc",
            "honeycomb_query_url": "https://...",
        }
        assert _claim_keys(_run_handle_healthy_finding(evidence)) == [
            "datadog_logs",
            "eks_pods",
            "grafana_logs",
        ]

    def test_random_custom_key_is_not_claimed(self) -> None:
        """Whitelist is authoritative: a truthy key not in the set stays out."""
        assert _claim_keys(_run_handle_healthy_finding({"my_new_custom_thing": [1]})) == []


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


class TestWhitelistIntegrity:
    """Guardrails on the ``CLAIM_EVIDENCE_KEYS`` whitelist itself."""

    def test_investigated_is_subset_of_claim_set(self) -> None:
        assert INVESTIGATED_EVIDENCE_KEYS <= CLAIM_EVIDENCE_KEYS

    def test_no_obvious_metadata_shapes_in_whitelist(self) -> None:
        """Sanity check: no whitelist entry looks like metadata."""
        obvious_metadata_suffixes = (
            "_query",
            "_count",
            "_ms",
            "_total",
            "_id",
            "_url",
            "_text",
            "_limit",
            "_window",
            "_by_pipeline",
        )
        offenders = [k for k in CLAIM_EVIDENCE_KEYS if k.endswith(obvious_metadata_suffixes)] + [
            k for k in CLAIM_EVIDENCE_KEYS if k.startswith("total_")
        ]
        assert not offenders, f"metadata-looking keys in whitelist: {offenders}"


def test_diagnose_root_cause_short_circuits_through_healthy_finding(monkeypatch) -> None:
    """End-to-end: the diagnose entry point routes a clearly-healthy state through
    ``_handle_healthy_finding`` without invoking the LLM, and the resulting
    validated claims come only from whitelisted evidence keys."""
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
            "grafana_logs_service": "api",
            "grafana_alert_rules_count": 0,
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
