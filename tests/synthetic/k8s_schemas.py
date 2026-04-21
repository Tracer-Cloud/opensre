"""
Schema definitions for Kubernetes synthetic testing fixtures.

All scenario fixture files (alert.json, eks_pods.json, eks_events.json,
eks_deployments.json, eks_node_health.json, eks_pod_logs.json,
datadog_logs.json, datadog_monitors.json, answer.yml, scenario.yml) must
conform to these TypedDicts.  Validators enforce required fields so every
scenario is structurally consistent.

The Kubernetes controlled vocabularies (engines, failure modes, evidence
sources, trajectory actions) are distinct from the RDS suite's and live
here rather than in ``tests/synthetic/schemas.py`` to keep the two suites
independently evolvable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired

from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Controlled vocabularies for scenario metadata
# ---------------------------------------------------------------------------

VALID_K8S_ENGINES = frozenset({"eks", "gke", "aks", "kubernetes"})

VALID_K8S_WORKLOAD_TYPES = frozenset(
    {"deployment", "statefulset", "daemonset", "job", "cronjob", "replicaset"}
)

VALID_K8S_FAILURE_MODES = frozenset(
    {
        "healthy",
        "crashloop_backoff",
        "oom_killed",
        "image_pull_backoff",
        "node_not_ready",
        "pending_pod",
        "deployment_rollout_stuck",
        "evicted_pods",
        "dns_resolution_failure",
        "probe_failure",
        "resource_quota_exceeded",
    }
)

VALID_K8S_EVIDENCE_SOURCES = frozenset(
    {
        "eks_pods",
        "eks_events",
        "eks_deployments",
        "eks_node_health",
        "eks_pod_logs",
        "datadog_logs",
        "datadog_monitors",
    }
)

VALID_K8S_TRAJECTORY_ACTIONS = frozenset(
    {
        "list_eks_pods",
        "get_eks_events",
        "list_eks_deployments",
        "get_eks_node_health",
        "get_eks_pod_logs",
        "query_datadog_logs",
        "query_datadog_monitors",
    }
)


# ---------------------------------------------------------------------------
# Alert fixture  (alert.json)
# ---------------------------------------------------------------------------


class K8sAlertLabels(TypedDict, total=False):
    alertname: str
    severity: str
    pipeline_name: str
    service: str
    cluster_name: str
    namespace: str
    workload_type: str
    workload_name: str


class K8sAlertAnnotations(TypedDict, total=False):
    summary: str
    description: str
    error: str
    suspected_symptom: str
    cluster_name: str
    kube_namespace: str
    kube_deployment: str
    kube_job: str
    kube_pod: str
    kube_node: str
    k8s_failure_mode: str
    context_sources: str


class K8sAlertFixture(TypedDict):
    title: str
    state: str
    alert_source: str
    commonLabels: K8sAlertLabels
    commonAnnotations: K8sAlertAnnotations


# ---------------------------------------------------------------------------
# EKS pods fixture  (eks_pods.json)
# Mirrors the shape returned by ``app.tools.EKSListPodsTool.list_eks_pods``.
# ---------------------------------------------------------------------------


class PodContainerState(TypedDict, total=False):
    running: bool
    waiting: bool
    terminated: bool
    reason: str
    message: str
    started_at: str
    exit_code: int


class PodContainer(TypedDict):
    name: str
    ready: bool
    restart_count: int
    state: PodContainerState


class PodCondition(TypedDict):
    type: str
    status: str
    reason: str
    message: str


class PodFixture(TypedDict):
    name: str
    namespace: str
    phase: str
    node_name: str
    containers: list[PodContainer]
    conditions: list[PodCondition]
    start_time: str


class EKSPodsFixture(TypedDict):
    pods: list[PodFixture]


# ---------------------------------------------------------------------------
# EKS events fixture  (eks_events.json)
# Mirrors the shape returned by ``app.tools.EKSEventsTool.get_eks_events``
# for a single Warning event record.
# ---------------------------------------------------------------------------


class EKSEvent(TypedDict):
    namespace: str
    reason: str
    message: str
    type: str
    count: int
    involved_object: str
    first_time: str
    last_time: str


class EKSEventsFixture(TypedDict):
    warning_events: list[EKSEvent]


# ---------------------------------------------------------------------------
# EKS deployments fixture  (eks_deployments.json)
# ---------------------------------------------------------------------------


class DeploymentFixture(TypedDict):
    """Matches the per-deployment shape returned by ``list_eks_deployments``."""

    name: str
    namespace: str
    desired: int
    ready: int
    available: int
    unavailable: int


class EKSDeploymentsFixture(TypedDict):
    deployments: list[DeploymentFixture]


# ---------------------------------------------------------------------------
# EKS node health fixture  (eks_node_health.json)
# ---------------------------------------------------------------------------


class NodeFixture(TypedDict, total=False):
    """Matches the per-node shape returned by ``get_eks_node_health``.

    Condition status fields (``ready``, ``memory_pressure``, etc.) are string
    values — Kubernetes condition statuses are ``"True"`` / ``"False"`` /
    ``"Unknown"``, not Python booleans.
    """

    name: str
    internal_ip: str
    ready: str
    memory_pressure: str
    disk_pressure: str
    pid_pressure: str
    capacity_cpu: str
    capacity_memory: str
    allocatable_cpu: str
    allocatable_memory: str
    instance_type: str


class EKSNodeHealthFixture(TypedDict):
    nodes: list[NodeFixture]


# ---------------------------------------------------------------------------
# EKS pod logs fixture  (eks_pod_logs.json)
# ---------------------------------------------------------------------------


class EKSPodLogsFixture(TypedDict):
    pod_name: str
    namespace: str
    logs: str


# ---------------------------------------------------------------------------
# Datadog logs fixture  (datadog_logs.json)
# Mirrors ``app.tools.DataDogLogsTool.query_datadog_logs``.
# ---------------------------------------------------------------------------


class DatadogLogEntry(TypedDict, total=False):
    timestamp: str
    message: str
    status: str
    service: str
    host: str
    tags: list[str]


class DatadogLogsFixture(TypedDict):
    logs: list[DatadogLogEntry]


# ---------------------------------------------------------------------------
# Datadog monitors fixture  (datadog_monitors.json)
# Mirrors ``app.tools.DataDogMonitorsTool.query_datadog_monitors``.
# ---------------------------------------------------------------------------


class DatadogMonitor(TypedDict, total=False):
    id: int
    name: str
    type: str
    query: str
    message: str
    overall_state: str
    tags: list[str]


class DatadogMonitorsFixture(TypedDict):
    monitors: list[DatadogMonitor]


# ---------------------------------------------------------------------------
# Answer key  (answer.yml)
# Same shape as the RDS answer key — duplicated here so the K8s suite is not
# coupled to the RDS trajectory vocabulary.
# ---------------------------------------------------------------------------


class K8sAnswerKeySchema(TypedDict):
    root_cause_category: str
    required_keywords: list[str]
    model_response: str
    forbidden_categories: NotRequired[list[str]]
    forbidden_keywords: NotRequired[list[str]]
    required_evidence_sources: NotRequired[list[str]]
    optimal_trajectory: NotRequired[list[str]]
    max_investigation_loops: NotRequired[int]
    ruling_out_keywords: NotRequired[list[str]]
    required_queries: NotRequired[list[str]]


# ---------------------------------------------------------------------------
# Scenario metadata  (scenario.yml)
# ---------------------------------------------------------------------------


class K8sScenarioMetadataSchema(TypedDict):
    schema_version: str
    scenario_id: str
    engine: str
    cluster_name: str
    namespace: str
    workload_type: str
    workload_name: str
    region: str
    failure_mode: str
    severity: str
    available_evidence: list[str]
    scenario_difficulty: NotRequired[int]
    adversarial_signals: NotRequired[list[str]]
    depends_on: NotRequired[str]


# ---------------------------------------------------------------------------
# Typed evidence container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class K8sScenarioEvidence:
    """Typed container for all evidence sources in a K8s scenario fixture.

    Each attribute is None when the corresponding file was not listed in
    scenario.yml:available_evidence, making evidence presence explicit.
    """

    eks_pods: EKSPodsFixture | None
    eks_events: EKSEventsFixture | None
    eks_deployments: EKSDeploymentsFixture | None
    eks_node_health: EKSNodeHealthFixture | None
    eks_pod_logs: EKSPodLogsFixture | None
    datadog_logs: DatadogLogsFixture | None
    datadog_monitors: DatadogMonitorsFixture | None

    def as_dict(self) -> dict[str, Any]:
        """Return only the non-None sources as a plain dict."""
        result: dict[str, Any] = {}
        if self.eks_pods is not None:
            result["eks_pods"] = self.eks_pods
        if self.eks_events is not None:
            result["eks_events"] = self.eks_events
        if self.eks_deployments is not None:
            result["eks_deployments"] = self.eks_deployments
        if self.eks_node_health is not None:
            result["eks_node_health"] = self.eks_node_health
        if self.eks_pod_logs is not None:
            result["eks_pod_logs"] = self.eks_pod_logs
        if self.datadog_logs is not None:
            result["datadog_logs"] = self.datadog_logs
        if self.datadog_monitors is not None:
            result["datadog_monitors"] = self.datadog_monitors
        return result

    def get(self, key: str) -> Any:
        return self.as_dict().get(key)


# ---------------------------------------------------------------------------
# Validators — raise ValueError with a descriptive message on bad data
# ---------------------------------------------------------------------------


def validate_k8s_alert(data: dict[str, Any]) -> K8sAlertFixture:
    _require_str(data, "title", ctx="alert.json")
    _require_str(data, "state", ctx="alert.json")
    _require_str(data, "alert_source", ctx="alert.json")
    if not isinstance(data.get("commonLabels"), dict):
        raise ValueError("alert.json: 'commonLabels' must be an object")
    if not isinstance(data.get("commonAnnotations"), dict):
        raise ValueError("alert.json: 'commonAnnotations' must be an object")
    return data  # type: ignore[return-value]


def validate_eks_pods(data: dict[str, Any]) -> EKSPodsFixture:
    ctx = "eks_pods.json"
    pods = data.get("pods")
    if not isinstance(pods, list):
        raise ValueError(f"{ctx}: 'pods' must be a list")
    for i, pod in enumerate(pods):
        pctx = f"{ctx}:pods[{i}]"
        for field in ("name", "namespace", "phase", "node_name", "start_time"):
            _require_str(pod, field, ctx=pctx)
        if not isinstance(pod.get("containers"), list):
            raise ValueError(f"{pctx}: 'containers' must be a list")
        if not isinstance(pod.get("conditions"), list):
            raise ValueError(f"{pctx}: 'conditions' must be a list")
        for j, container in enumerate(pod["containers"]):
            cctx = f"{pctx}:containers[{j}]"
            _require_str(container, "name", ctx=cctx)
            if not isinstance(container.get("ready"), bool):
                raise ValueError(f"{cctx}: 'ready' must be a boolean")
            if not isinstance(container.get("restart_count"), int):
                raise ValueError(f"{cctx}: 'restart_count' must be an integer")
            if not isinstance(container.get("state"), dict):
                raise ValueError(f"{cctx}: 'state' must be an object")
    return data  # type: ignore[return-value]


def validate_eks_events(data: dict[str, Any]) -> EKSEventsFixture:
    ctx = "eks_events.json"
    events = data.get("warning_events")
    if not isinstance(events, list):
        raise ValueError(f"{ctx}: 'warning_events' must be a list")
    for i, event in enumerate(events):
        ectx = f"{ctx}:warning_events[{i}]"
        for field in (
            "namespace",
            "reason",
            "message",
            "type",
            "involved_object",
            "first_time",
            "last_time",
        ):
            _require_str(event, field, ctx=ectx)
        if not isinstance(event.get("count"), int):
            raise ValueError(f"{ectx}: 'count' must be an integer")
    return data  # type: ignore[return-value]


def validate_eks_deployments(data: dict[str, Any]) -> EKSDeploymentsFixture:
    ctx = "eks_deployments.json"
    deployments = data.get("deployments")
    if not isinstance(deployments, list):
        raise ValueError(f"{ctx}: 'deployments' must be a list")
    for i, deployment in enumerate(deployments):
        dctx = f"{ctx}:deployments[{i}]"
        _require_str(deployment, "name", ctx=dctx)
        _require_str(deployment, "namespace", ctx=dctx)
        for int_field in ("desired", "ready", "available", "unavailable"):
            if not isinstance(deployment.get(int_field), int):
                raise ValueError(f"{dctx}: '{int_field}' must be an integer")
    return data  # type: ignore[return-value]


def validate_eks_node_health(data: dict[str, Any]) -> EKSNodeHealthFixture:
    ctx = "eks_node_health.json"
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError(f"{ctx}: 'nodes' must be a list")
    for i, node in enumerate(nodes):
        nctx = f"{ctx}:nodes[{i}]"
        _require_str(node, "name", ctx=nctx)
        ready = node.get("ready")
        if not isinstance(ready, str) or ready not in ("True", "False", "Unknown"):
            raise ValueError(
                f"{nctx}: 'ready' must be the string 'True', 'False', or 'Unknown' "
                "(Kubernetes condition status)"
            )
    return data  # type: ignore[return-value]


def validate_eks_pod_logs(data: dict[str, Any]) -> EKSPodLogsFixture:
    ctx = "eks_pod_logs.json"
    _require_str(data, "pod_name", ctx=ctx)
    _require_str(data, "namespace", ctx=ctx)
    if not isinstance(data.get("logs"), str):
        raise ValueError(f"{ctx}: 'logs' must be a string")
    return data  # type: ignore[return-value]


def validate_datadog_logs(data: dict[str, Any]) -> DatadogLogsFixture:
    ctx = "datadog_logs.json"
    logs = data.get("logs")
    if not isinstance(logs, list):
        raise ValueError(f"{ctx}: 'logs' must be a list")
    for i, entry in enumerate(logs):
        lctx = f"{ctx}:logs[{i}]"
        for field in ("timestamp", "message"):
            _require_str(entry, field, ctx=lctx)
        if "tags" in entry and not isinstance(entry["tags"], list):
            raise ValueError(f"{lctx}: 'tags' must be a list when present")
    return data  # type: ignore[return-value]


def validate_datadog_monitors(data: dict[str, Any]) -> DatadogMonitorsFixture:
    ctx = "datadog_monitors.json"
    monitors = data.get("monitors")
    if not isinstance(monitors, list):
        raise ValueError(f"{ctx}: 'monitors' must be a list")
    for i, monitor in enumerate(monitors):
        mctx = f"{ctx}:monitors[{i}]"
        for field in ("name", "type", "query", "overall_state"):
            _require_str(monitor, field, ctx=mctx)
    return data  # type: ignore[return-value]


def validate_k8s_answer_key(data: dict[str, Any]) -> K8sAnswerKeySchema:
    _require_str(data, "root_cause_category", ctx="answer.yml")
    _require_non_empty_str_list(data, "required_keywords", "answer.yml", required=True)
    _require_str(data, "model_response", ctx="answer.yml")
    for opt_list_field in (
        "forbidden_categories",
        "forbidden_keywords",
        "required_evidence_sources",
    ):
        val = data.get(opt_list_field)
        if val is not None and not isinstance(val, list):
            raise ValueError(f"answer.yml: '{opt_list_field}' must be a list when present")
    trajectory = data.get("optimal_trajectory")
    if trajectory is not None:
        if not isinstance(trajectory, list) or not trajectory:
            raise ValueError(
                "answer.yml: 'optimal_trajectory' must be a non-empty list when present"
            )
        unknown_actions = [a for a in trajectory if a not in VALID_K8S_TRAJECTORY_ACTIONS]
        if unknown_actions:
            raise ValueError(
                f"answer.yml: unknown action(s) in optimal_trajectory {unknown_actions}; "
                f"expected subset of {sorted(VALID_K8S_TRAJECTORY_ACTIONS)}"
            )
    max_loops = data.get("max_investigation_loops")
    if max_loops is not None and (not isinstance(max_loops, int) or max_loops < 1):
        raise ValueError(
            "answer.yml: 'max_investigation_loops' must be a positive integer when present"
        )
    for axis2_list_field in ("ruling_out_keywords", "required_queries"):
        _require_non_empty_str_list(data, axis2_list_field, "answer.yml")
    return data  # type: ignore[return-value]


def validate_k8s_scenario_metadata(data: dict[str, Any]) -> K8sScenarioMetadataSchema:
    ctx = "scenario.yml"
    for field in (
        "schema_version",
        "scenario_id",
        "engine",
        "cluster_name",
        "namespace",
        "workload_type",
        "workload_name",
        "region",
        "failure_mode",
        "severity",
    ):
        _require_str(data, field, ctx=ctx)

    engine = data["engine"]
    if engine not in VALID_K8S_ENGINES:
        raise ValueError(
            f"{ctx}: unknown engine {engine!r}; expected one of {sorted(VALID_K8S_ENGINES)}"
        )

    workload_type = data["workload_type"]
    if workload_type not in VALID_K8S_WORKLOAD_TYPES:
        raise ValueError(
            f"{ctx}: unknown workload_type {workload_type!r}; expected one of {sorted(VALID_K8S_WORKLOAD_TYPES)}"
        )

    failure_mode = data["failure_mode"]
    if failure_mode not in VALID_K8S_FAILURE_MODES:
        raise ValueError(
            f"{ctx}: unknown failure_mode {failure_mode!r}; expected one of {sorted(VALID_K8S_FAILURE_MODES)}"
        )

    sources = data.get("available_evidence")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{ctx}: 'available_evidence' must be a non-empty list")
    unknown = [s for s in sources if s not in VALID_K8S_EVIDENCE_SOURCES]
    if unknown:
        raise ValueError(
            f"{ctx}: unknown evidence source(s) {unknown}; "
            f"expected subset of {sorted(VALID_K8S_EVIDENCE_SOURCES)}"
        )

    return data  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_str(obj: dict[str, Any], key: str, ctx: str = "") -> None:
    value = obj.get(key)
    prefix = f"{ctx}: " if ctx else ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{prefix}missing or empty required string field '{key}'")


def _require_non_empty_str_list(
    obj: dict[str, Any],
    key: str,
    ctx: str,
    *,
    required: bool = False,
) -> None:
    value = obj.get(key)

    if value is None:
        if required:
            raise ValueError(f"{ctx}: '{key}' must be a non-empty list")
        return

    if not isinstance(value, list) or not value:
        raise ValueError(f"{ctx}: '{key}' must be a non-empty list")

    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{ctx}: all '{key}' entries must be non-empty strings")
