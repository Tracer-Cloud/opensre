"""Tests for EKS evidence catalog in report_context."""

from __future__ import annotations

from app.nodes.publish_findings.report_context import (
    ReportContext,
    _add_eks_deployments,
    _add_eks_events,
    _add_eks_node_health,
    _add_eks_pod_logs,
    _add_eks_pods,
)


class TestEKSAddPods:
    """Test _add_eks_pods catalog builder."""

    def test_add_eks_pods_with_failing(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_pods": [
                {"name": "healthy-pod", "phase": "Running", "containers": []},
                {
                    "name": "failing-pod",
                    "phase": "Failed",
                    "containers": [{"name": "app", "restart_count": 0}],
                },
            ],
            "eks_failing_pods": [
                {
                    "name": "failing-pod",
                    "phase": "Failed",
                    "containers": [
                        {
                            "name": "app",
                            "restart_count": 0,
                            "state": {"terminated": True, "exit_code": 1, "reason": "Error"},
                        }
                    ],
                },
            ],
            "eks_high_restart_pods": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_pods(evidence, catalog, source_to_id)

        assert "evidence/eks/prod-cluster-1/tracer/pod/failing-pod" in catalog
        assert source_to_id["eks_pods"] == "evidence/eks/prod-cluster-1/tracer/pod/failing-pod"

        entry = catalog["evidence/eks/prod-cluster-1/tracer/pod/failing-pod"]
        assert entry["label"] == "EKS Pod: failing-pod"
        assert "cluster=prod-cluster-1" in entry["summary"]
        assert "namespace=tracer" in entry["summary"]

    def test_add_eks_pods_no_failing(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_pods": [
                {"name": "healthy-pod", "phase": "Running", "containers": []},
            ],
            "eks_failing_pods": [],
            "eks_high_restart_pods": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_pods(evidence, catalog, source_to_id)

        assert "evidence/eks/prod-cluster-1/tracer/pods" in catalog
        assert source_to_id["eks_pods"] == "evidence/eks/prod-cluster-1/tracer/pods"

    def test_add_eks_pods_no_pods(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_pods": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_pods(evidence, catalog, source_to_id)

        assert not catalog
        assert "eks_pods" not in source_to_id

    def test_add_eks_pods_handles_missing_container_name(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_pods": [{"name": "failing-pod", "phase": "Failed"}],
            "eks_failing_pods": [
                {
                    "name": "failing-pod",
                    "phase": "Failed",
                    "containers": [
                        {"state": {"waiting": True, "reason": "CrashLoopBackOff"}},
                    ],
                }
            ],
            "eks_high_restart_pods": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_pods(evidence, catalog, source_to_id)

        entry = catalog["evidence/eks/prod-cluster-1/tracer/pod/failing-pod"]
        assert entry["snippet"] is not None
        assert "unknown: waiting" in entry["snippet"]


class TestEKSAddDeployments:
    """Test _add_eks_deployments catalog builder."""

    def test_add_eks_deployments_with_degraded(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_deployments": [
                {"name": "healthy-dep", "desired": 3, "ready": 3, "degraded": False},
                {
                    "name": "degraded-dep",
                    "desired": 3,
                    "ready": 1,
                    "unavailable": 2,
                    "degraded": True,
                },
            ],
            "eks_degraded_deployments": [
                {
                    "name": "degraded-dep",
                    "desired": 3,
                    "ready": 1,
                    "unavailable": 2,
                    "degraded": True,
                },
            ],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_deployments(evidence, catalog, source_to_id)

        assert "evidence/eks/prod-cluster-1/tracer/deployment/degraded-dep" in catalog
        entry = catalog["evidence/eks/prod-cluster-1/tracer/deployment/degraded-dep"]
        assert entry["label"] == "EKS Deployment: degraded-dep"

    def test_add_eks_deployments_no_degraded(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_deployments": [
                {"name": "healthy-dep", "desired": 3, "ready": 3, "degraded": False},
            ],
            "eks_degraded_deployments": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_deployments(evidence, catalog, source_to_id)

        assert "evidence/eks/prod-cluster-1/tracer/deployments" in catalog

    def test_add_eks_deployments_no_data(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_deployments": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_deployments(evidence, catalog, source_to_id)

        assert not catalog


class TestEKSAddEvents:
    """Test _add_eks_events catalog builder."""

    def test_add_eks_events(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_warning_events": [
                {"reason": "OOMKilled", "message": "Container killed", "count": 5},
                {"reason": "BackOff", "message": "Restarting", "count": 3},
            ],
            "eks_total_warning_count": 8,
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_events(evidence, catalog, source_to_id)

        assert "evidence/eks/prod-cluster-1/tracer/events" in catalog
        entry = catalog["evidence/eks/prod-cluster-1/tracer/events"]
        assert entry["label"] == "EKS Warning Events (prod-cluster-1)"
        assert "cluster=prod-cluster-1" in entry["summary"]
        assert "total=8" in entry["summary"]

    def test_add_eks_events_no_events(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_warning_events": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_events(evidence, catalog, source_to_id)

        assert not catalog


class TestEKSAddPodLogs:
    """Test _add_eks_pod_logs catalog builder."""

    def test_add_eks_pod_logs(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_pod_name": "crashing-pod",
            "eks_pod_logs": "ERROR: OutOfMemory\nTraceback: ...",
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_pod_logs(evidence, catalog, source_to_id)

        assert "evidence/eks/prod-cluster-1/tracer/pod/crashing-pod/logs" in catalog
        entry = catalog["evidence/eks/prod-cluster-1/tracer/pod/crashing-pod/logs"]
        assert entry["label"] == "EKS Pod Logs: crashing-pod"
        assert "cluster=prod-cluster-1" in entry["summary"]
        assert "pod=crashing-pod" in entry["summary"]

    def test_add_eks_pod_logs_no_logs(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_pod_name": "crashing-pod",
            "eks_pod_logs": "",
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_pod_logs(evidence, catalog, source_to_id)

        assert not catalog


class TestEKSAddNodeHealth:
    """Test _add_eks_node_health catalog builder."""

    def test_add_eks_node_health(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_nodes": [
                {"name": "node-1", "status": "Ready"},
                {"name": "node-2", "status": "Ready"},
                {"name": "node-3", "status": "NotReady"},
            ],
            "eks_not_ready_nodes": [
                {
                    "name": "node-3",
                    "status": "NotReady",
                    "conditions": [{"type": "Ready", "status": "False"}],
                },
            ],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_node_health(evidence, catalog, source_to_id)

        assert "evidence/eks/prod-cluster-1/nodes" in catalog
        entry = catalog["evidence/eks/prod-cluster-1/nodes"]
        assert entry["label"] == "EKS Node Health (prod-cluster-1)"
        assert "cluster=prod-cluster-1" in entry["summary"]
        assert "nodes=3" in entry["summary"]

    def test_add_eks_node_health_no_nodes(self) -> None:
        evidence = {
            "eks_cluster_name": "prod-cluster-1",
            "eks_nodes": [],
        }
        catalog: dict = {}
        source_to_id: dict = {}

        _add_eks_node_health(evidence, catalog, source_to_id)

        assert not catalog


class TestEKSSourceAliases:
    """Test EKS sources are properly aliased for claim linking."""

    def test_eks_source_aliases_exist(self) -> None:
        from app.nodes.publish_findings.report_context import _SOURCE_ALIASES

        assert _SOURCE_ALIASES["eks"] == "eks_pods"
        assert _SOURCE_ALIASES["eks_pods"] == "eks_pods"
        assert _SOURCE_ALIASES["eks_deployments"] == "eks_deployments"
        assert _SOURCE_ALIASES["eks_events"] == "eks_events"
        assert _SOURCE_ALIASES["eks_pod_logs"] == "eks_pod_logs"
        assert _SOURCE_ALIASES["eks_node_health"] == "eks_node_health"


class TestEKSReportContextFields:
    """Test ReportContext includes EKS provenance fields."""

    def test_report_context_has_eks_fields(self) -> None:
        # Verify the type annotation accepts EKS fields
        ctx: ReportContext = {
            "pipeline_name": "test",
            "root_cause": "Out of memory",
            "validated_claims": [],
            "eks_cluster_name": "prod-cluster-1",
            "eks_namespace": "tracer",
            "eks_pod_name": "crashing-pod",
            "eks_deployment_name": "worker-dep",
        }

        assert ctx["eks_cluster_name"] == "prod-cluster-1"
        assert ctx["eks_namespace"] == "tracer"
        assert ctx["eks_pod_name"] == "crashing-pod"
        assert ctx["eks_deployment_name"] == "worker-dep"
