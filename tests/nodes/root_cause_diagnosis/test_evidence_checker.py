"""Tests for ``app/nodes/root_cause_diagnosis/evidence_checker.py``.

Focuses on the ``is_clearly_healthy`` helper that gates the healthy
short-circuit in ``diagnose_root_cause``.  In particular, these tests verify
that the recently-added ``eks_*`` entries in ``INVESTIGATED_EVIDENCE_KEYS``
make pure-Kubernetes healthy investigations fast-path out of the reasoning
LLM the same way Grafana-only and Datadog-only healthy states already do.
"""

from __future__ import annotations

import pytest

from app.nodes.root_cause_diagnosis.evidence_checker import is_clearly_healthy


def _healthy_alert() -> dict:
    return {
        "state": "normal",
        "alert_source": "datadog",
        "commonLabels": {"severity": "info"},
        "commonAnnotations": {},
    }


class TestIsClearlyHealthyEKSEvidence:
    """Pure-Kubernetes healthy states must satisfy the evidence gate."""

    def test_pure_eks_pods_evidence_triggers_short_circuit(self) -> None:
        evidence = {
            "eks_pods": [{"name": "payments-api-x", "phase": "Running"}],
        }
        assert is_clearly_healthy(_healthy_alert(), evidence) is True

    def test_pure_eks_events_evidence_triggers_short_circuit(self) -> None:
        # An empty warning_events list after a completed investigation is
        # itself a healthy signal — no events found means no warnings.
        evidence = {"eks_events": []}
        assert is_clearly_healthy(_healthy_alert(), evidence) is True

    def test_pure_eks_deployments_evidence_triggers_short_circuit(self) -> None:
        evidence = {
            "eks_deployments": [
                {"name": "payments-api", "desired": 3, "ready": 3, "available": 3, "unavailable": 0}
            ],
        }
        assert is_clearly_healthy(_healthy_alert(), evidence) is True

    def test_pure_eks_node_health_evidence_triggers_short_circuit(self) -> None:
        evidence = {
            "eks_node_health": [
                {"name": "ip-10-0-1-42", "ready": "True"},
                {"name": "ip-10-0-1-73", "ready": "True"},
            ],
        }
        assert is_clearly_healthy(_healthy_alert(), evidence) is True

    def test_pure_eks_pod_logs_evidence_triggers_short_circuit(self) -> None:
        evidence = {"eks_pod_logs": "started http server on :8080"}
        assert is_clearly_healthy(_healthy_alert(), evidence) is True

    def test_combined_eks_evidence_triggers_short_circuit(self) -> None:
        evidence = {
            "eks_pods": [{"name": "payments-api-x", "phase": "Running"}],
            "eks_events": [],
            "eks_deployments": [
                {"name": "payments-api", "desired": 3, "ready": 3, "available": 3, "unavailable": 0}
            ],
            "eks_node_health": [{"name": "ip-10-0-1-42", "ready": "True"}],
            "eks_pod_logs": "started http server on :8080",
        }
        assert is_clearly_healthy(_healthy_alert(), evidence) is True

    def test_mixed_eks_and_datadog_evidence_triggers_short_circuit(self) -> None:
        # Mixed evidence (EKS + Datadog) must also satisfy the gate — verifies
        # the EKS keys interoperate with the existing investigated-evidence keys.
        evidence = {
            "eks_pods": [{"name": "payments-api-x", "phase": "Running"}],
            "datadog_logs": [],
        }
        assert is_clearly_healthy(_healthy_alert(), evidence) is True


class TestIsClearlyHealthyExistingSources:
    """Non-regression coverage for the evidence sources that already worked."""

    @pytest.mark.parametrize(
        "evidence_key",
        [
            "grafana_logs",
            "grafana_metrics",
            "grafana_alert_rules",
            "aws_cloudwatch_metrics",
            "aws_rds_events",
            "aws_performance_insights",
            "cloudwatch_logs",
            "datadog_logs",
            "datadog_monitors",
        ],
    )
    def test_single_existing_evidence_key_triggers_short_circuit(self, evidence_key: str) -> None:
        evidence = {evidence_key: []}
        assert is_clearly_healthy(_healthy_alert(), evidence) is True


class TestIsClearlyHealthyRejectsUnhealthyStates:
    """Every gate condition must still reject non-healthy alerts."""

    def test_no_evidence_returns_false(self) -> None:
        assert is_clearly_healthy(_healthy_alert(), {}) is False

    def test_alerting_state_returns_false(self) -> None:
        alert = _healthy_alert()
        alert["state"] = "alerting"
        evidence = {"eks_pods": [{"name": "x"}]}
        assert is_clearly_healthy(alert, evidence) is False

    def test_critical_severity_returns_false(self) -> None:
        alert = _healthy_alert()
        alert["commonLabels"] = {"severity": "critical"}
        evidence = {"eks_pods": [{"name": "x"}]}
        assert is_clearly_healthy(alert, evidence) is False

    def test_error_annotation_returns_false(self) -> None:
        alert = _healthy_alert()
        alert["commonAnnotations"] = {"error": "something went wrong"}
        evidence = {"eks_pods": [{"name": "x"}]}
        assert is_clearly_healthy(alert, evidence) is False

    def test_unknown_evidence_key_does_not_trigger_short_circuit(self) -> None:
        """A key outside INVESTIGATED_EVIDENCE_KEYS alone is not enough."""
        evidence = {"random_custom_key": "some value"}
        assert is_clearly_healthy(_healthy_alert(), evidence) is False

    def test_non_dict_alert_returns_false(self) -> None:
        evidence = {"eks_pods": [{"name": "x"}]}
        assert is_clearly_healthy("raw text alert", evidence) is False
