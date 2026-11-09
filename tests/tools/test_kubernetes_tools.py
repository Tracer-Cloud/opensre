"""Tests for Kubernetes investigation tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.kubernetes.tools import (
    KubernetesDescribePodTool,
    KubernetesGetEventsTool,
    KubernetesGetPodLogsTool,
    KubernetesGetResourceTool,
    KubernetesListConfigMapsTool,
    KubernetesListDaemonSetsTool,
    KubernetesListDeploymentsTool,
    KubernetesListIngressesTool,
    KubernetesListNodesTool,
    KubernetesListPodsTool,
    KubernetesListServicesTool,
    KubernetesListStatefulSetsTool,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state

_MINIMAL_KUBECONFIG = (
    "apiVersion: v1\n"
    "clusters: []\n"
    "contexts: []\n"
    "current-context: ''\n"
    "kind: Config\n"
    "preferences: {}\n"
    "users: []\n"
)

_K8S_SOURCE = {
    "connection_verified": True,
    "kubeconfig": _MINIMAL_KUBECONFIG,
    "context": "",
    "namespace": "default",
}


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


class TestKubernetesListPodsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListPodsTool()


class TestKubernetesGetPodLogsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetPodLogsTool()


class TestKubernetesListDeploymentsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListDeploymentsTool()


class TestKubernetesGetEventsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetEventsTool()


class TestKubernetesDescribePodContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesDescribePodTool()


class TestKubernetesListNodesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListNodesTool()


class TestKubernetesListServicesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListServicesTool()


class TestKubernetesListStatefulSetsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListStatefulSetsTool()


class TestKubernetesListDaemonSetsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListDaemonSetsTool()


class TestKubernetesListIngressesContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListIngressesTool()


class TestKubernetesListConfigMapsContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesListConfigMapsTool()


class TestKubernetesGetResourceContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return KubernetesGetResourceTool()


# ---------------------------------------------------------------------------
# is_available / extract_params
# ---------------------------------------------------------------------------


def test_list_pods_is_available_requires_kubeconfig() -> None:
    tool = KubernetesListPodsTool()
    assert tool.is_available({"kubernetes": _K8S_SOURCE}) is True
    assert tool.is_available({}) is False
    assert tool.is_available({"kubernetes": {}}) is False
    assert tool.is_available({"kubernetes": {"kubeconfig": ""}}) is False


def test_list_pods_extract_params_maps_fields() -> None:
    tool = KubernetesListPodsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["kubeconfig"] == sources["kubernetes"]["kubeconfig"]
    assert params["namespace"] == "default"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_mock_pod(name: str, phase: str = "Running") -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.metadata.labels = {"app": "test"}
    pod.metadata.creation_timestamp = None
    pod.status.phase = phase
    pod.status.conditions = []
    pod.status.container_statuses = []
    pod.spec.node_name = "node-1"
    return pod


def _make_client_with_core(mock_core: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = mock_core
    client._apps_v1 = MagicMock()
    client._networking_v1 = MagicMock()
    return client


def _make_client_with_apps(mock_apps: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = MagicMock()
    client._apps_v1 = mock_apps
    client._networking_v1 = MagicMock()
    return client


def _make_client_with_networking(mock_networking: MagicMock) -> Any:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    client = KubernetesClient(cfg)
    client._core_v1 = MagicMock()
    client._apps_v1 = MagicMock()
    client._networking_v1 = mock_networking
    return client


# ---------------------------------------------------------------------------
# list_pods run()
# ---------------------------------------------------------------------------


def test_list_pods_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_pod_list = MagicMock()
    mock_pod_list.items = [_make_mock_pod("web-abc"), _make_mock_pod("web-xyz")]

    mock_core = MagicMock()
    mock_core.list_namespaced_pod.return_value = mock_pod_list

    tool = KubernetesListPodsTool()

    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 2
    assert result["pods"][0]["name"] == "web-abc"


def test_list_pods_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListPodsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


def test_list_pods_run_returns_unavailable_on_api_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from kubernetes.client.exceptions import ApiException

    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_core.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesListPodsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is False
    assert "403" in result["error"]


# ---------------------------------------------------------------------------
# get_pod_logs run()
# ---------------------------------------------------------------------------


def test_get_pod_logs_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    raw_response = MagicMock()
    raw_response.data = b"line1\nline2\nline3"
    mock_core = MagicMock()
    mock_core.read_namespaced_pod_log.return_value = raw_response
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetPodLogsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            pod_name="web-abc",
            namespace="default",
        )

    assert result["available"] is True
    assert result["total"] == 3
    assert result["lines"] == ["line1", "line2", "line3"]
    assert result["pod_name"] == "web-abc"


def test_get_pod_logs_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesGetPodLogsTool()
    result = tool.run(kubeconfig="", pod_name="web-abc")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_deployments run()
# ---------------------------------------------------------------------------


def _make_mock_deployment(name: str, desired: int = 3, ready: int = 3) -> MagicMock:
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.namespace = "default"
    dep.metadata.labels = {}
    dep.metadata.creation_timestamp = None
    dep.spec.replicas = desired
    dep.status.ready_replicas = ready
    dep.status.available_replicas = ready
    dep.status.unavailable_replicas = desired - ready
    dep.status.updated_replicas = ready
    return dep


def test_list_deployments_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_apps = MagicMock()
    mock_dep_list = MagicMock()
    mock_dep_list.items = [_make_mock_deployment("api", desired=3, ready=2)]
    mock_apps.list_namespaced_deployment.return_value = mock_dep_list
    mock_client._core_v1 = MagicMock()
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesListDeploymentsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["deployments"][0]["name"] == "api"
    assert result["deployments"][0]["unavailable"] == 1


# ---------------------------------------------------------------------------
# get_events run()
# ---------------------------------------------------------------------------


def _make_mock_event(name: str, reason: str = "CrashLoopBackOff") -> MagicMock:
    ev = MagicMock()
    ev.metadata.name = name
    ev.metadata.namespace = "default"
    ev.reason = reason
    ev.message = f"Back-off restarting failed container: {reason}"
    ev.type = "Warning"
    ev.count = 5
    ev.involved_object.kind = "Pod"
    ev.involved_object.name = "web-abc"
    ev.involved_object.namespace = "default"
    ev.first_timestamp = None
    ev.last_timestamp = None
    return ev


def test_get_events_run_happy_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_ev_list = MagicMock()
    mock_ev_list.items = [_make_mock_event("ev-1")]
    mock_core.list_namespaced_event.return_value = mock_ev_list
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetEventsTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["events"][0]["reason"] == "CrashLoopBackOff"
    assert result["events"][0]["type"] == "Warning"


# ---------------------------------------------------------------------------
# describe_pod run()
# ---------------------------------------------------------------------------


def _make_mock_pod_detail(name: str) -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.metadata.labels = {"app": "web"}
    pod.metadata.annotations = {}
    pod.metadata.creation_timestamp = None
    pod.metadata.owner_references = []
    pod.spec.node_name = "node-1"
    pod.spec.service_account_name = "default"
    pod.spec.node_selector = {}
    pod.spec.volumes = []
    pod.spec.init_containers = []
    c = MagicMock()
    c.name = "app"
    c.image = "nginx:1.25"
    c.ports = []
    c.resources.requests = {"cpu": "100m"}
    c.resources.limits = {"memory": "256Mi"}
    c.env = []
    pod.spec.containers = [c]
    pod.status.phase = "Running"
    pod.status.host_ip = "10.0.0.1"
    pod.status.pod_ip = "192.168.1.5"
    pod.status.conditions = []
    pod.status.container_statuses = []
    pod.status.init_container_statuses = []
    return pod


def test_describe_pod_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = _make_mock_pod_detail("web-abc")

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    assert result["available"] is True
    assert result["name"] == "web-abc"
    assert result["spec"]["node_name"] == "node-1"
    assert result["status"]["phase"] == "Running"
    assert result["spec"]["containers"][0]["image"] == "nginx:1.25"


def test_describe_pod_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesDescribePodTool()
    result = tool.run(kubeconfig="", pod_name="web-abc")
    assert result["available"] is False


def test_describe_pod_run_includes_valuefrom_env_names_without_values() -> None:
    literal_env = MagicMock()
    literal_env.name = "LOG_LEVEL"
    literal_env.value = "debug"
    secret_env = MagicMock()
    secret_env.name = "DB_PASSWORD"
    secret_env.value = None

    pod = _make_mock_pod_detail("web-abc")
    pod.spec.containers[0].env = [literal_env, secret_env]

    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = pod

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    env_names = result["spec"]["containers"][0]["env"]
    assert env_names == ["LOG_LEVEL", "DB_PASSWORD"]
    assert "debug" not in env_names


def test_describe_pod_run_strips_last_applied_config_annotation() -> None:
    pod = _make_mock_pod_detail("web-abc")
    pod.metadata.annotations = {
        "kubectl.kubernetes.io/last-applied-configuration": (
            '{"spec":{"containers":[{"env":[{"name":"DB_PASSWORD","value":"hunter2"}]}]}}'
        ),
        "some-other/annotation": "keep-me",
    }

    mock_core = MagicMock()
    mock_core.read_namespaced_pod.return_value = pod

    tool = KubernetesDescribePodTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, pod_name="web-abc", namespace="default")

    assert "kubectl.kubernetes.io/last-applied-configuration" not in result["annotations"]
    assert result["annotations"]["some-other/annotation"] == "keep-me"


# ---------------------------------------------------------------------------
# list_nodes run()
# ---------------------------------------------------------------------------


def _make_mock_node(name: str, ready: bool = True) -> MagicMock:
    node = MagicMock()
    node.metadata.name = name
    node.metadata.labels = {"kubernetes.io/hostname": name}
    node.metadata.creation_timestamp = None
    node.spec.taints = []
    cond = MagicMock()
    cond.type = "Ready"
    cond.status = "True" if ready else "False"
    cond.reason = "KubeletReady"
    cond.message = "kubelet is posting ready status"
    node.status.conditions = [cond]
    node.status.capacity = {"cpu": "4", "memory": "8Gi"}
    node.status.allocatable = {"cpu": "3900m", "memory": "7Gi"}
    return node


def test_list_nodes_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_node_list = MagicMock()
    mock_node_list.items = [_make_mock_node("node-1"), _make_mock_node("node-2", ready=False)]
    mock_core.list_node.return_value = mock_node_list

    tool = KubernetesListNodesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG)

    assert result["available"] is True
    assert result["total"] == 2
    assert result["nodes"][0]["name"] == "node-1"
    assert result["nodes"][0]["conditions"][0]["type"] == "Ready"
    assert result["nodes"][0]["conditions"][0]["status"] == "True"
    assert result["nodes"][1]["conditions"][0]["status"] == "False"


def test_list_nodes_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListNodesTool()
    result = tool.run(kubeconfig="")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_services run()
# ---------------------------------------------------------------------------


def _make_mock_service(name: str, svc_type: str = "ClusterIP") -> MagicMock:
    svc = MagicMock()
    svc.metadata.name = name
    svc.metadata.namespace = "default"
    svc.metadata.labels = {}
    svc.metadata.creation_timestamp = None
    svc.spec.type = svc_type
    svc.spec.cluster_ip = "10.96.0.1"
    svc.spec.external_i_ps = []
    svc.spec.selector = {"app": name}
    port = MagicMock()
    port.name = "http"
    port.port = 80
    port.target_port = 8080
    port.protocol = "TCP"
    port.node_port = None
    svc.spec.ports = [port]
    return svc


def test_list_services_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_svc_list = MagicMock()
    mock_svc_list.items = [_make_mock_service("api"), _make_mock_service("frontend")]
    mock_core.list_namespaced_service.return_value = mock_svc_list

    tool = KubernetesListServicesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 2
    assert result["services"][0]["name"] == "api"
    assert result["services"][0]["type"] == "ClusterIP"
    assert result["services"][0]["ports"][0]["port"] == 80


def test_list_services_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListServicesTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_statefulsets run()
# ---------------------------------------------------------------------------


def _make_mock_statefulset(name: str, desired: int = 3, ready: int = 3) -> MagicMock:
    sts = MagicMock()
    sts.metadata.name = name
    sts.metadata.namespace = "default"
    sts.metadata.labels = {}
    sts.metadata.creation_timestamp = None
    sts.spec.replicas = desired
    sts.status.ready_replicas = ready
    sts.status.current_replicas = ready
    sts.status.updated_replicas = ready
    return sts


def test_list_statefulsets_run_happy_path() -> None:
    mock_apps = MagicMock()
    mock_sts_list = MagicMock()
    mock_sts_list.items = [_make_mock_statefulset("postgres", desired=3, ready=2)]
    mock_apps.list_namespaced_stateful_set.return_value = mock_sts_list

    tool = KubernetesListStatefulSetsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_apps(mock_apps),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["statefulsets"][0]["name"] == "postgres"
    assert result["statefulsets"][0]["desired"] == 3
    assert result["statefulsets"][0]["ready"] == 2


def test_list_statefulsets_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListStatefulSetsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_daemonsets run()
# ---------------------------------------------------------------------------


def _make_mock_daemonset(name: str, desired: int = 5, ready: int = 5) -> MagicMock:
    ds = MagicMock()
    ds.metadata.name = name
    ds.metadata.namespace = "default"
    ds.metadata.labels = {}
    ds.metadata.creation_timestamp = None
    ds.status.desired_number_scheduled = desired
    ds.status.current_number_scheduled = desired
    ds.status.number_ready = ready
    ds.status.updated_number_scheduled = ready
    ds.status.number_available = ready
    return ds


def test_list_daemonsets_run_happy_path() -> None:
    mock_apps = MagicMock()
    mock_ds_list = MagicMock()
    mock_ds_list.items = [_make_mock_daemonset("fluentd", desired=5, ready=4)]
    mock_apps.list_namespaced_daemon_set.return_value = mock_ds_list

    tool = KubernetesListDaemonSetsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_apps(mock_apps),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["daemonsets"][0]["name"] == "fluentd"
    assert result["daemonsets"][0]["desired"] == 5
    assert result["daemonsets"][0]["ready"] == 4


def test_list_daemonsets_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListDaemonSetsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_ingresses run()
# ---------------------------------------------------------------------------


def _make_mock_ingress(name: str, host: str = "api.example.com") -> MagicMock:
    ing = MagicMock()
    ing.metadata.name = name
    ing.metadata.namespace = "default"
    ing.metadata.labels = {}
    ing.metadata.creation_timestamp = None
    ing.spec.ingress_class_name = "nginx"
    path = MagicMock()
    path.path = "/api"
    path.path_type = "Prefix"
    path.backend.service.name = "api-svc"
    path.backend.service.port.number = 80
    rule = MagicMock()
    rule.host = host
    rule.http.paths = [path]
    ing.spec.rules = [rule]
    ing.spec.tls = []
    ing.status.load_balancer.ingress = []
    return ing


def test_list_ingresses_run_happy_path() -> None:
    mock_networking = MagicMock()
    mock_ing_list = MagicMock()
    mock_ing_list.items = [_make_mock_ingress("api-ingress")]
    mock_networking.list_namespaced_ingress.return_value = mock_ing_list

    tool = KubernetesListIngressesTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_networking(mock_networking),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["ingresses"][0]["name"] == "api-ingress"
    assert result["ingresses"][0]["ingress_class"] == "nginx"
    assert result["ingresses"][0]["rules"][0]["host"] == "api.example.com"
    assert result["ingresses"][0]["rules"][0]["paths"][0]["service_name"] == "api-svc"


def test_list_ingresses_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListIngressesTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# list_configmaps run()
# ---------------------------------------------------------------------------


def _make_mock_configmap(name: str, data: dict[str, str] | None = None) -> MagicMock:
    cm = MagicMock()
    cm.metadata.name = name
    cm.metadata.namespace = "default"
    cm.metadata.labels = {}
    cm.metadata.creation_timestamp = None
    cm.data = data or {"key1": "value1", "key2": "value2"}
    return cm


def test_list_configmaps_run_happy_path() -> None:
    mock_core = MagicMock()
    mock_cm_list = MagicMock()
    mock_cm_list.items = [
        _make_mock_configmap("app-config", {"DB_HOST": "postgres:5432", "LOG_LEVEL": "info"})
    ]
    mock_core.list_namespaced_config_map.return_value = mock_cm_list

    tool = KubernetesListConfigMapsTool()
    with patch(
        "integrations.kubernetes.tools._make_client",
        return_value=_make_client_with_core(mock_core),
    ):
        result = tool.run(kubeconfig=_MINIMAL_KUBECONFIG, namespace="default")

    assert result["available"] is True
    assert result["total"] == 1
    assert result["configmaps"][0]["name"] == "app-config"
    assert result["configmaps"][0]["data"]["DB_HOST"] == "postgres:5432"
    assert "DB_HOST" in result["configmaps"][0]["data_keys"]


def test_list_configmaps_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesListConfigMapsTool()
    result = tool.run(kubeconfig="", namespace="default")
    assert result["available"] is False
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# get_resource run()
# ---------------------------------------------------------------------------


def test_get_resource_run_happy_path() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_core = MagicMock()
    mock_dep = MagicMock()
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.return_value = mock_dep
    mock_client._core_v1 = mock_core
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()
    # simulate sanitize_for_serialization
    mock_client._api_client = MagicMock()
    mock_client._api_client.sanitize_for_serialization.return_value = {
        "kind": "Deployment",
        "metadata": {"name": "api"},
    }

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="api",
            namespace="default",
        )

    assert result["available"] is True
    assert result["resource_type"] == "deployment"
    assert result["name"] == "api"
    assert result["resource"]["kind"] == "Deployment"


def test_get_resource_run_strips_last_applied_config_annotation() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_client._core_v1 = MagicMock()
    mock_apps = MagicMock()
    mock_apps.read_namespaced_deployment.return_value = MagicMock()
    mock_client._apps_v1 = mock_apps
    mock_client._networking_v1 = MagicMock()
    mock_client._api_client = MagicMock()
    mock_client._api_client.sanitize_for_serialization.return_value = {
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": (
                    '{"spec":{"template":{"spec":{"containers":'
                    '[{"env":[{"name":"DB_PASSWORD","value":"hunter2"}]}]}}}}'
                ),
                "some-other/annotation": "keep-me",
            },
        },
    }

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG,
            resource_type="deployment",
            name="api",
            namespace="default",
        )

    annotations = result["resource"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/last-applied-configuration" not in annotations
    assert annotations["some-other/annotation"] == "keep-me"


def test_get_resource_run_unsupported_type() -> None:
    from integrations.config_models import KubernetesIntegrationConfig
    from integrations.kubernetes.client import KubernetesClient

    cfg = KubernetesIntegrationConfig.model_validate({"kubeconfig": _MINIMAL_KUBECONFIG})
    mock_client = KubernetesClient(cfg)
    mock_client._core_v1 = MagicMock()
    mock_client._apps_v1 = MagicMock()
    mock_client._networking_v1 = MagicMock()

    tool = KubernetesGetResourceTool()
    with patch("integrations.kubernetes.tools._make_client", return_value=mock_client):
        result = tool.run(
            kubeconfig=_MINIMAL_KUBECONFIG, resource_type="foobar", name="x", namespace="default"
        )

    assert result["available"] is False
    assert "foobar" in result["error"]


def test_get_resource_run_returns_unavailable_when_no_client() -> None:
    tool = KubernetesGetResourceTool()
    result = tool.run(kubeconfig="", resource_type="deployment", name="api")
    assert result["available"] is False
