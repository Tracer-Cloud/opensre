"""Tests for evidence mapping in ``app/nodes/investigate/processing/post_process.py``.

These tests verify that each registered mapper converts a successfully-executed
tool result into the expected state-evidence shape, and that
``merge_evidence`` correctly merges the mapped fields into the agent's
evidence dict.
"""

from __future__ import annotations

from app.nodes.investigate.execution.execute_actions import ActionExecutionResult
from app.nodes.investigate.processing.post_process import (
    EVIDENCE_MAPPERS,
    build_evidence_summary,
    merge_evidence,
)


def _result(action_name: str, data: dict, success: bool = True) -> ActionExecutionResult:
    return ActionExecutionResult(
        action_name=action_name,
        success=success,
        data=data,
        error=None,
    )


class TestEKSMappersRegistered:
    """Every fixture-backed EKS tool must have an entry in ``EVIDENCE_MAPPERS``."""

    def test_list_eks_pods_registered(self) -> None:
        assert "list_eks_pods" in EVIDENCE_MAPPERS

    def test_get_eks_events_registered(self) -> None:
        assert "get_eks_events" in EVIDENCE_MAPPERS

    def test_list_eks_deployments_registered(self) -> None:
        assert "list_eks_deployments" in EVIDENCE_MAPPERS

    def test_get_eks_node_health_registered(self) -> None:
        assert "get_eks_node_health" in EVIDENCE_MAPPERS

    def test_get_eks_pod_logs_registered(self) -> None:
        assert "get_eks_pod_logs" in EVIDENCE_MAPPERS


class TestListEKSPodsMapper:
    """list_eks_pods → eks_pods / eks_failing_pods / eks_high_restart_pods / eks_total_pods."""

    def test_populates_all_pod_keys(self) -> None:
        data = {
            "source": "eks",
            "available": True,
            "cluster_name": "payments-prod-eks",
            "namespace": "payments",
            "total_pods": 3,
            "pods": [
                {"name": "pod-1", "phase": "Running", "containers": []},
                {"name": "pod-2", "phase": "CrashLoopBackOff", "containers": []},
                {"name": "pod-3", "phase": "Running", "containers": []},
            ],
            "failing_pods": [{"name": "pod-2", "phase": "CrashLoopBackOff", "containers": []}],
            "high_restart_pods": [],
            "error": None,
        }
        evidence = merge_evidence({}, {"list_eks_pods": _result("list_eks_pods", data)})
        assert evidence["eks_total_pods"] == 3
        assert len(evidence["eks_pods"]) == 3
        assert evidence["eks_failing_pods"] == [
            {"name": "pod-2", "phase": "CrashLoopBackOff", "containers": []}
        ]
        assert evidence["eks_high_restart_pods"] == []

    def test_defaults_when_fields_missing(self) -> None:
        evidence = merge_evidence({}, {"list_eks_pods": _result("list_eks_pods", {})})
        assert evidence["eks_pods"] == []
        assert evidence["eks_failing_pods"] == []
        assert evidence["eks_high_restart_pods"] == []
        assert evidence["eks_total_pods"] == 0


class TestGetEKSEventsMapper:
    """get_eks_events → eks_events / eks_total_warning_count."""

    def test_populates_event_keys(self) -> None:
        data = {
            "source": "eks",
            "available": True,
            "warning_events": [
                {
                    "namespace": "payments",
                    "reason": "OOMKilled",
                    "message": "container exceeded memory limit",
                    "type": "Warning",
                    "count": 1,
                    "involved_object": "Pod/payments-api-7f9-x7g",
                    "first_time": "2026-04-14T10:12:00Z",
                    "last_time": "2026-04-14T10:12:00Z",
                }
            ],
            "total_warning_count": 1,
            "error": None,
        }
        evidence = merge_evidence({}, {"get_eks_events": _result("get_eks_events", data)})
        assert len(evidence["eks_events"]) == 1
        assert evidence["eks_events"][0]["reason"] == "OOMKilled"
        assert evidence["eks_total_warning_count"] == 1

    def test_empty_events_for_healthy_state(self) -> None:
        data = {"warning_events": [], "total_warning_count": 0}
        evidence = merge_evidence({}, {"get_eks_events": _result("get_eks_events", data)})
        assert evidence["eks_events"] == []
        assert evidence["eks_total_warning_count"] == 0


class TestListEKSDeploymentsMapper:
    """list_eks_deployments → eks_deployments / eks_degraded_deployments / eks_total_deployments."""

    def test_populates_deployment_keys_with_degraded_subset(self) -> None:
        data = {
            "source": "eks",
            "deployments": [
                {"name": "payments-api", "desired": 3, "ready": 3, "available": 3, "unavailable": 0, "degraded": False},
                {"name": "payments-worker", "desired": 2, "ready": 0, "available": 0, "unavailable": 2, "degraded": True},
            ],
            "degraded_deployments": [
                {"name": "payments-worker", "desired": 2, "ready": 0, "available": 0, "unavailable": 2, "degraded": True}
            ],
            "total_deployments": 2,
        }
        evidence = merge_evidence(
            {}, {"list_eks_deployments": _result("list_eks_deployments", data)}
        )
        assert evidence["eks_total_deployments"] == 2
        assert len(evidence["eks_deployments"]) == 2
        assert len(evidence["eks_degraded_deployments"]) == 1
        assert evidence["eks_degraded_deployments"][0]["name"] == "payments-worker"


class TestGetEKSNodeHealthMapper:
    """get_eks_node_health → eks_node_health / eks_not_ready_count / eks_total_nodes."""

    def test_populates_node_keys(self) -> None:
        data = {
            "source": "eks",
            "nodes": [
                {"name": "node-1", "ready": "True"},
                {"name": "node-2", "ready": "False"},
            ],
            "total_nodes": 2,
            "not_ready_count": 1,
        }
        evidence = merge_evidence(
            {}, {"get_eks_node_health": _result("get_eks_node_health", data)}
        )
        assert evidence["eks_total_nodes"] == 2
        assert evidence["eks_not_ready_count"] == 1
        assert len(evidence["eks_node_health"]) == 2


class TestGetEKSPodLogsMapper:
    """get_eks_pod_logs → eks_pod_logs / eks_pod_logs_pod_name / eks_pod_logs_namespace."""

    def test_populates_log_keys(self) -> None:
        data = {
            "source": "eks",
            "cluster_name": "payments-prod-eks",
            "namespace": "payments",
            "pod_name": "payments-api-7f9-x7g",
            "logs": "line 1\nline 2\nfatal: out of memory",
        }
        evidence = merge_evidence(
            {}, {"get_eks_pod_logs": _result("get_eks_pod_logs", data)}
        )
        assert "fatal: out of memory" in evidence["eks_pod_logs"]
        assert evidence["eks_pod_logs_pod_name"] == "payments-api-7f9-x7g"
        assert evidence["eks_pod_logs_namespace"] == "payments"


class TestMergeEvidenceSkipsFailedResults:
    """``merge_evidence`` must not apply the mapper when ``result.success`` is False."""

    def test_failed_list_eks_pods_is_skipped(self) -> None:
        data = {"pods": [{"name": "pod-1"}], "total_pods": 1}
        result = ActionExecutionResult(
            action_name="list_eks_pods",
            success=False,
            data=data,
            error="Unable to assume IAM role",
        )
        evidence = merge_evidence({}, {"list_eks_pods": result})
        assert "eks_pods" not in evidence
        assert "eks_total_pods" not in evidence


class TestBuildEvidenceSummaryEKS:
    """``build_evidence_summary`` must emit human-readable EKS entries."""

    def test_list_eks_pods_summary_with_failing(self) -> None:
        data = {"pods": [{}, {}, {}], "failing_pods": [{}], "total_pods": 3}
        summary = build_evidence_summary(
            {"list_eks_pods": _result("list_eks_pods", data)}
        )
        assert "eks:3 pods" in summary
        assert "(1 failing)" in summary

    def test_list_eks_pods_summary_with_no_failing(self) -> None:
        data = {"pods": [{}], "failing_pods": [], "total_pods": 1}
        summary = build_evidence_summary(
            {"list_eks_pods": _result("list_eks_pods", data)}
        )
        assert "eks:1 pods" in summary
        assert "(0 failing)" in summary

    def test_get_eks_events_summary(self) -> None:
        data = {"warning_events": [{}, {}], "total_warning_count": 2}
        summary = build_evidence_summary(
            {"get_eks_events": _result("get_eks_events", data)}
        )
        assert "eks:2 warning events" in summary

    def test_list_eks_deployments_summary(self) -> None:
        data = {
            "deployments": [{}, {}],
            "degraded_deployments": [{}],
            "total_deployments": 2,
        }
        summary = build_evidence_summary(
            {"list_eks_deployments": _result("list_eks_deployments", data)}
        )
        assert "eks:2 deployments" in summary
        assert "(1 degraded)" in summary

    def test_get_eks_node_health_summary(self) -> None:
        data = {"nodes": [{}, {}, {}], "total_nodes": 3, "not_ready_count": 1}
        summary = build_evidence_summary(
            {"get_eks_node_health": _result("get_eks_node_health", data)}
        )
        assert "eks:3 nodes" in summary
        assert "(1 not ready)" in summary

    def test_get_eks_pod_logs_summary(self) -> None:
        data = {
            "pod_name": "payments-api-7f9",
            "logs": "line 1\nline 2\nline 3",
        }
        summary = build_evidence_summary(
            {"get_eks_pod_logs": _result("get_eks_pod_logs", data)}
        )
        assert "eks:3 log lines" in summary
        assert "payments-api-7f9" in summary
