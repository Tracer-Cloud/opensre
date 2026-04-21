"""EKSBackend Protocol and FixtureEKSBackend for synthetic K8s testing.

The Protocol defines the minimal surface the Kubernetes investigation agent
uses to query EKS workload state.  FixtureEKSBackend satisfies it by serving
scenario fixture data in the exact shape the EKS tools under ``app/tools/EKS*/``
return — no HTTP calls, no AWS credentials required.

Usage
-----
    resolved_integrations = {
        "eks": {
            "cluster_name": "",
            "role_arn": "",
            "_backend": FixtureEKSBackend(fixture),
        }
    }

Each tool's production resolver checks the ``_backend`` key first and delegates
to it when present, falling back to real EKS API calls when absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tests.synthetic.eks.scenario_loader import K8sScenarioFixture


@runtime_checkable
class EKSBackend(Protocol):
    """Minimal EKS interface used by the Kubernetes investigation agent.

    One method per evidence source under ``app/tools/EKS*/``:
        list_pods        → EKSListPodsTool response shape
        get_events       → EKSEventsTool response shape
        list_deployments → EKSListDeploymentsTool response shape
        get_node_health  → EKSNodeHealthTool response shape
        get_pod_logs     → EKSPodLogsTool response shape
    """

    def list_pods(
        self, cluster_name: str = "", namespace: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        """Return a response matching ``list_eks_pods``."""

    def get_events(
        self, cluster_name: str = "", namespace: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        """Return a response matching ``get_eks_events``."""

    def list_deployments(
        self, cluster_name: str = "", namespace: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        """Return a response matching ``list_eks_deployments``."""

    def get_node_health(self, cluster_name: str = "", **kwargs: Any) -> dict[str, Any]:
        """Return a response matching ``get_eks_node_health``."""

    def get_pod_logs(
        self, cluster_name: str = "", namespace: str = "", pod_name: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        """Return a response matching ``get_eks_pod_logs``."""


class FixtureEKSBackend:
    """EKSBackend implementation backed by a K8sScenarioFixture.

    Each method wraps the corresponding fixture file in the envelope that the
    real tool function returns.  Calling a method for an evidence source that
    the scenario did not declare in ``available_evidence`` raises ValueError.
    """

    def __init__(self, fixture: K8sScenarioFixture) -> None:
        self._fixture = fixture

    def _cluster_name(self, override: str) -> str:
        return override or self._fixture.metadata.cluster_name

    def _namespace(self, override: str) -> str:
        return override or self._fixture.metadata.namespace

    def list_pods(self, cluster_name: str = "", namespace: str = "", **_: Any) -> dict[str, Any]:
        pods_fixture = self._fixture.evidence.eks_pods
        if pods_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: list_pods called but "
                "'eks_pods' is not declared in available_evidence"
            )
        pods = list(pods_fixture.get("pods", []))
        failing_pods = [p for p in pods if p.get("phase") not in ("Running", "Succeeded")]
        high_restart_pods = [
            p for p in pods if any(c.get("restart_count", 0) > 3 for c in p.get("containers", []))
        ]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": self._cluster_name(cluster_name),
            "namespace": self._namespace(namespace),
            "total_pods": len(pods),
            "pods": pods,
            "failing_pods": failing_pods,
            "high_restart_pods": high_restart_pods,
            "error": None,
        }

    def get_events(self, cluster_name: str = "", namespace: str = "", **_: Any) -> dict[str, Any]:
        events_fixture = self._fixture.evidence.eks_events
        if events_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: get_events called but "
                "'eks_events' is not declared in available_evidence"
            )
        warning_events = list(events_fixture.get("warning_events", []))
        return {
            "source": "eks",
            "available": True,
            "cluster_name": self._cluster_name(cluster_name),
            "namespace": self._namespace(namespace),
            "warning_events": warning_events,
            "total_warning_count": len(warning_events),
            "error": None,
        }

    def list_deployments(
        self, cluster_name: str = "", namespace: str = "", **_: Any
    ) -> dict[str, Any]:
        deployments_fixture = self._fixture.evidence.eks_deployments
        if deployments_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: list_deployments called but "
                "'eks_deployments' is not declared in available_evidence"
            )
        deployments: list[dict[str, Any]] = []
        for raw in deployments_fixture.get("deployments", []):
            desired = int(raw.get("desired", 0))
            ready = int(raw.get("ready", 0))
            available = int(raw.get("available", 0))
            unavailable = int(raw.get("unavailable", 0))
            deployments.append(
                {
                    "name": raw.get("name", ""),
                    "namespace": raw.get("namespace", ""),
                    "desired": desired,
                    "ready": ready,
                    "available": available,
                    "unavailable": unavailable,
                    "degraded": unavailable > 0 or ready < desired,
                }
            )
        degraded = [d for d in deployments if d["degraded"]]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": self._cluster_name(cluster_name),
            "namespace": self._namespace(namespace),
            "total_deployments": len(deployments),
            "deployments": deployments,
            "degraded_deployments": degraded,
            "error": None,
        }

    def get_node_health(self, cluster_name: str = "", **_: Any) -> dict[str, Any]:
        nodes_fixture = self._fixture.evidence.eks_node_health
        if nodes_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: get_node_health called but "
                "'eks_node_health' is not declared in available_evidence"
            )
        nodes = list(nodes_fixture.get("nodes", []))
        not_ready_count = sum(1 for n in nodes if n.get("ready") != "True")
        return {
            "source": "eks",
            "available": True,
            "cluster_name": self._cluster_name(cluster_name),
            "nodes": nodes,
            "total_nodes": len(nodes),
            "not_ready_count": not_ready_count,
            "error": None,
        }

    def get_pod_logs(
        self,
        cluster_name: str = "",
        namespace: str = "",
        pod_name: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        logs_fixture = self._fixture.evidence.eks_pod_logs
        if logs_fixture is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: get_pod_logs called but "
                "'eks_pod_logs' is not declared in available_evidence"
            )
        return {
            "source": "eks",
            "available": True,
            "cluster_name": self._cluster_name(cluster_name),
            "namespace": self._namespace(namespace) or logs_fixture.get("namespace", ""),
            "pod_name": pod_name or logs_fixture.get("pod_name", ""),
            "logs": logs_fixture.get("logs", ""),
            "error": None,
        }
