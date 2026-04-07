"""Tests for EKS evidence mapping in post_process."""

from __future__ import annotations

import pytest

from app.nodes.investigate.processing.post_process import (
    EVIDENCE_MAPPERS,
    build_evidence_summary,
    merge_evidence,
)


class TestEKSEvidenceMappers:
    """Test EKS tool results are correctly mapped to evidence."""

    def test_mapper_handles_non_dict_data(self) -> None:
        """Test type guards return empty dict for non-dict input."""
        from app.nodes.investigate.processing.post_process import (
            _map_eks_deployments,
            _map_eks_events,
            _map_eks_namespaces,
            _map_eks_node_health,
            _map_eks_pod_logs,
            _map_eks_pods,
        )

        # All mappers should return empty dict for non-dict input
        assert _map_eks_namespaces(None) == {}  # type: ignore[arg-type]
        assert _map_eks_pods([]) == {}  # type: ignore[arg-type]
        assert _map_eks_deployments("string") == {}  # type: ignore[arg-type]
        assert _map_eks_events(123) == {}  # type: ignore[arg-type]
        assert _map_eks_pod_logs(None) == {}  # type: ignore[arg-type]
        assert _map_eks_node_health({"invalid"}) == {}  # type: ignore[arg-type]

    def test_list_eks_namespaces_mapper(self) -> None:
        data = {
            "cluster_name": "prod-cluster-1",
            "namespaces": [
                {"name": "default", "status": "Active"},
                {"name": "tracer", "status": "Active"},
            ],
        }
        mapper = EVIDENCE_MAPPERS["list_eks_namespaces"]
        result = mapper(data)

        assert result["eks_cluster_name"] == "prod-cluster-1"
        assert len(result["eks_namespaces"]) == 2
        assert result["eks_namespaces"][0]["name"] == "default"

    def test_list_eks_pods_mapper(self) -> None:
        data = {
            "cluster_name": "prod-cluster-1",
            "namespace": "tracer",
            "pods": [
                {
                    "name": "api-pod-1",
                    "namespace": "tracer",
                    "phase": "Running",
                    "containers": [{"name": "api", "ready": True, "restart_count": 0}],
                },
                {
                    "name": "crashing-pod",
                    "namespace": "tracer",
                    "phase": "Failed",
                    "containers": [{"name": "worker", "ready": False, "restart_count": 5}],
                },
            ],
            "failing_pods": [
                {
                    "name": "crashing-pod",
                    "namespace": "tracer",
                    "phase": "Failed",
                    "containers": [{"name": "worker", "ready": False, "restart_count": 5}],
                },
            ],
            "high_restart_pods": [
                {
                    "name": "crashing-pod",
                    "namespace": "tracer",
                    "phase": "Failed",
                    "containers": [{"name": "worker", "ready": False, "restart_count": 5}],
                },
            ],
        }
        mapper = EVIDENCE_MAPPERS["list_eks_pods"]
        result = mapper(data)

        assert result["eks_cluster_name"] == "prod-cluster-1"
        assert result["eks_namespace"] == "tracer"
        assert len(result["eks_pods"]) == 2
        assert len(result["eks_failing_pods"]) == 1
        assert len(result["eks_high_restart_pods"]) == 1

    def test_list_eks_deployments_mapper(self) -> None:
        data = {
            "cluster_name": "prod-cluster-1",
            "namespace": "tracer",
            "deployments": [
                {
                    "name": "api-deployment",
                    "desired": 3,
                    "ready": 3,
                    "available": 3,
                    "unavailable": 0,
                    "degraded": False,
                },
                {
                    "name": "worker-deployment",
                    "desired": 3,
                    "ready": 1,
                    "available": 1,
                    "unavailable": 2,
                    "degraded": True,
                },
            ],
            "degraded_deployments": [
                {
                    "name": "worker-deployment",
                    "desired": 3,
                    "ready": 1,
                    "available": 1,
                    "unavailable": 2,
                    "degraded": True,
                },
            ],
        }
        mapper = EVIDENCE_MAPPERS["list_eks_deployments"]
        result = mapper(data)

        assert result["eks_cluster_name"] == "prod-cluster-1"
        assert result["eks_namespace"] == "tracer"
        assert len(result["eks_deployments"]) == 2
        assert len(result["eks_degraded_deployments"]) == 1

    def test_get_eks_events_mapper(self) -> None:
        data = {
            "cluster_name": "prod-cluster-1",
            "namespace": "tracer",
            "warning_events": [
                {
                    "namespace": "tracer",
                    "reason": "OOMKilled",
                    "message": "Container killed due to memory limit",
                    "type": "Warning",
                    "count": 5,
                    "involved_object": "Pod/crashing-pod",
                },
            ],
            "total_warning_count": 5,
        }
        mapper = EVIDENCE_MAPPERS["get_eks_events"]
        result = mapper(data)

        assert result["eks_cluster_name"] == "prod-cluster-1"
        assert result["eks_namespace"] == "tracer"
        assert len(result["eks_warning_events"]) == 1
        assert result["eks_total_warning_count"] == 5

    def test_get_eks_pod_logs_mapper(self) -> None:
        data = {
            "cluster_name": "prod-cluster-1",
            "namespace": "tracer",
            "pod_name": "api-pod-1",
            "logs": "ERROR: Connection timeout\nTraceback: ...",
        }
        mapper = EVIDENCE_MAPPERS["get_eks_pod_logs"]
        result = mapper(data)

        assert result["eks_cluster_name"] == "prod-cluster-1"
        assert result["eks_namespace"] == "tracer"
        assert result["eks_pod_name"] == "api-pod-1"
        assert result["eks_pod_logs"] == "ERROR: Connection timeout\nTraceback: ..."

    def test_get_eks_node_health_mapper(self) -> None:
        data = {
            "cluster_name": "prod-cluster-1",
            "nodes": [
                {"name": "node-1", "status": "Ready", "conditions": []},
                {
                    "name": "node-2",
                    "status": "NotReady",
                    "conditions": [{"type": "Ready", "status": "False"}],
                },
            ],
            "not_ready_nodes": [
                {
                    "name": "node-2",
                    "status": "NotReady",
                    "conditions": [{"type": "Ready", "status": "False"}],
                },
            ],
        }
        mapper = EVIDENCE_MAPPERS["get_eks_node_health"]
        result = mapper(data)

        assert result["eks_cluster_name"] == "prod-cluster-1"
        assert len(result["eks_nodes"]) == 2
        assert len(result["eks_not_ready_nodes"]) == 1


class TestEKSEvidenceMerging:
    """Test EKS evidence is merged correctly."""

    def test_merge_eks_pods_evidence(self) -> None:
        current_evidence: dict = {}
        execution_results = {
            "list_eks_pods": type(
                "Result",
                (),
                {
                    "success": True,
                    "data": {
                        "cluster_name": "prod-cluster-1",
                        "namespace": "tracer",
                        "pods": [{"name": "pod-1", "phase": "Running"}],
                        "failing_pods": [],
                        "high_restart_pods": [],
                    },
                },
            )(),
        }

        result = merge_evidence(current_evidence, execution_results)

        assert result["eks_cluster_name"] == "prod-cluster-1"
        assert result["eks_namespace"] == "tracer"
        assert len(result["eks_pods"]) == 1


class TestEKSEvidenceSummary:
    """Test EKS evidence summary generation."""

    def test_summary_list_eks_namespaces(self) -> None:
        execution_results = {
            "list_eks_namespaces": type(
                "Result",
                (),
                {
                    "success": True,
                    "data": {
                        "cluster_name": "prod-cluster-1",
                        "namespaces": [{"name": "default"}, {"name": "tracer"}],
                    },
                },
            )(),
        }

        summary = build_evidence_summary(execution_results)

        assert "eks:prod-cluster-1:2 namespaces" in summary

    def test_summary_list_eks_pods(self) -> None:
        execution_results = {
            "list_eks_pods": type(
                "Result",
                (),
                {
                    "success": True,
                    "data": {
                        "cluster_name": "prod-cluster-1",
                        "namespace": "tracer",
                        "pods": [{"name": "pod-1"}, {"name": "pod-2"}],
                        "failing_pods": [{"name": "pod-2"}],
                        "high_restart_pods": [],
                    },
                },
            )(),
        }

        summary = build_evidence_summary(execution_results)

        assert "eks:prod-cluster-1:tracer:2 pods (1 failing)" in summary

    def test_summary_list_eks_deployments(self) -> None:
        execution_results = {
            "list_eks_deployments": type(
                "Result",
                (),
                {
                    "success": True,
                    "data": {
                        "cluster_name": "prod-cluster-1",
                        "namespace": "tracer",
                        "deployments": [{"name": "dep-1"}, {"name": "dep-2"}],
                        "degraded_deployments": [{"name": "dep-2"}],
                    },
                },
            )(),
        }

        summary = build_evidence_summary(execution_results)

        assert "eks:prod-cluster-1:tracer:2 deployments (1 degraded)" in summary

    def test_summary_get_eks_events(self) -> None:
        execution_results = {
            "get_eks_events": type(
                "Result",
                (),
                {
                    "success": True,
                    "data": {
                        "cluster_name": "prod-cluster-1",
                        "namespace": "tracer",
                        "warning_events": [{"reason": "OOMKilled"}],
                        "total_warning_count": 3,
                    },
                },
            )(),
        }

        summary = build_evidence_summary(execution_results)

        assert "eks:prod-cluster-1:tracer:3 warning events" in summary

    def test_summary_get_eks_pod_logs(self) -> None:
        execution_results = {
            "get_eks_pod_logs": type(
                "Result",
                (),
                {
                    "success": True,
                    "data": {
                        "cluster_name": "prod-cluster-1",
                        "namespace": "tracer",
                        "pod_name": "crashing-pod",
                        "logs": "Error: OutOfMemory",
                    },
                },
            )(),
        }

        summary = build_evidence_summary(execution_results)

        assert "eks:prod-cluster-1:tracer:pod/crashing-pod logs" in summary

    def test_summary_get_eks_node_health(self) -> None:
        execution_results = {
            "get_eks_node_health": type(
                "Result",
                (),
                {
                    "success": True,
                    "data": {
                        "cluster_name": "prod-cluster-1",
                        "nodes": [{"name": "node-1"}, {"name": "node-2"}, {"name": "node-3"}],
                        "not_ready_nodes": [{"name": "node-3"}],
                    },
                },
            )(),
        }

        summary = build_evidence_summary(execution_results)

        assert "eks:prod-cluster-1:3 nodes (1 not ready)" in summary

    def test_summary_failed_action(self) -> None:
        execution_results = {
            "list_eks_pods": type(
                "Result",
                (),
                {
                    "success": False,
                    "data": {},
                    "error": "Connection refused to cluster",
                },
            )(),
        }

        summary = build_evidence_summary(execution_results)

        assert "list_eks_pods:FAILED" in summary
