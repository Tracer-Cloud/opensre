"""SelectiveEKSBackend — query-aware mock for Axis 2 adversarial tests.

Unlike FixtureEKSBackend (which returns every available pod, event, and
deployment on each call), this backend records every namespace and pod name
the agent requested so tests can assert the agent asked for the right things.

Filtering the returned slice by the requested namespace / pod is reserved for
a future iteration — the current action registry does not yet narrow requests
tightly enough for meaningful filtering to help.

Usage in test_suite_axis2.py
-----------------------------
    backend = SelectiveEKSBackend(fixture)
    final_state, score = run_scenario(fixture, use_mock_backends=True, eks_backend=backend)
    assert "payments" in backend.queried_namespaces
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from tests.synthetic.mock_eks_backend.backend import FixtureEKSBackend

if TYPE_CHECKING:
    from tests.synthetic.eks.scenario_loader import K8sScenarioFixture


class SelectiveEKSBackend(FixtureEKSBackend):
    """Query-aware EKSBackend for Axis 2 adversarial testing.

    Satisfies the EKSBackend Protocol by inheriting every method from
    FixtureEKSBackend, then augments each call with an audit record.
    """

    def __init__(self, fixture: K8sScenarioFixture) -> None:
        super().__init__(fixture)
        self.queried_namespaces: list[str] = []
        self.queried_pod_names: list[str] = []
        self.queried_tools: list[str] = []

    def list_pods(self, cluster_name: str = "", namespace: str = "", **kwargs: Any) -> dict[str, Any]:
        self.queried_tools.append("list_pods")
        self.queried_namespaces.append(self._namespace(namespace))
        return super().list_pods(cluster_name=cluster_name, namespace=namespace, **kwargs)

    def get_events(self, cluster_name: str = "", namespace: str = "", **kwargs: Any) -> dict[str, Any]:
        self.queried_tools.append("get_events")
        self.queried_namespaces.append(self._namespace(namespace))
        return super().get_events(cluster_name=cluster_name, namespace=namespace, **kwargs)

    def list_deployments(
        self, cluster_name: str = "", namespace: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        self.queried_tools.append("list_deployments")
        self.queried_namespaces.append(self._namespace(namespace))
        return super().list_deployments(cluster_name=cluster_name, namespace=namespace, **kwargs)

    def get_node_health(self, cluster_name: str = "", **kwargs: Any) -> dict[str, Any]:
        self.queried_tools.append("get_node_health")
        return super().get_node_health(cluster_name=cluster_name, **kwargs)

    def get_pod_logs(
        self,
        cluster_name: str = "",
        namespace: str = "",
        pod_name: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.queried_tools.append("get_pod_logs")
        self.queried_namespaces.append(self._namespace(namespace))
        self.queried_pod_names.append(pod_name)
        return super().get_pod_logs(
            cluster_name=cluster_name, namespace=namespace, pod_name=pod_name, **kwargs
        )

    def reset(self) -> None:
        """Clear the audit log (useful for re-running a scenario)."""
        self.queried_namespaces = []
        self.queried_pod_names = []
        self.queried_tools = []

    @property
    def unique_queried_tools(self) -> set[str]:
        """Deduplicated set of all tool methods that were invoked."""
        return set(self.queried_tools)

    def queried(self, tool_name: str) -> bool:
        """Return True if ``tool_name`` was ever invoked (exact match)."""
        return tool_name in self.queried_tools

    def __deepcopy__(self, memo: dict) -> SelectiveEKSBackend:
        new = SelectiveEKSBackend(self._fixture)
        new.queried_namespaces = copy.deepcopy(self.queried_namespaces, memo)
        new.queried_pod_names = copy.deepcopy(self.queried_pod_names, memo)
        new.queried_tools = copy.deepcopy(self.queried_tools, memo)
        return new
