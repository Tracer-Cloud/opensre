"""Kubernetes investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from integrations.config_models import KubernetesIntegrationConfig
from integrations.kubernetes.client import _RESOURCE_DISPATCH, KubernetesClient

_RESOURCE_TYPE_ENUM: list[str] = sorted(_RESOURCE_DISPATCH.keys())


def _make_client(sources: dict[str, Any]) -> KubernetesClient | None:
    k8s = sources.get("kubernetes", {})
    kubeconfig = k8s.get("kubeconfig", "")
    kubeconfig_path = k8s.get("kubeconfig_path", "")
    if not kubeconfig and not kubeconfig_path:
        return None
    try:
        cfg = KubernetesIntegrationConfig.model_validate(
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": k8s.get("context", ""),
                "namespace": k8s.get("namespace", "default"),
            }
        )
        return KubernetesClient(cfg)
    except Exception:
        return None


def _is_available(sources: dict[str, Any]) -> bool:
    k8s = sources.get("kubernetes", {})
    return bool(k8s.get("kubeconfig") or k8s.get("kubeconfig_path"))


def _missing_client_error(extra: dict[str, Any]) -> dict[str, Any]:
    return tool_unavailable(
        "kubernetes", "Kubernetes integration is not configured (missing kubeconfig).", **extra
    )


_SHARED_KUBECONFIG_PROPS: dict[str, Any] = {
    "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
    "kubeconfig_path": {
        "type": "string",
        "default": "",
        "description": "Path to kubeconfig file (alternative to kubeconfig)",
    },
    "context": {"type": "string", "default": "", "description": "Kubeconfig context to use"},
    "namespace": {
        "type": "string",
        "default": "default",
        "description": "Kubernetes namespace to target",
    },
}


class KubernetesListPodsTool(BaseTool):
    """List pods in a Kubernetes namespace to diagnose availability and restart issues."""

    name = "kubernetes_list_pods"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List pods in a Kubernetes namespace. Returns pod phase, container readiness, "
        "restart counts, and node assignment. Use to detect crash-looping or pending pods."
    )
    use_cases = [
        "Checking if pods are in a crash-loop or pending state",
        "Identifying which pods are not ready or have high restart counts",
        "Filtering pods by label selector to scope investigation",
        "Verifying that a deployment's pods are running after a rollout",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["kubeconfig"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "label_selector": {
                "type": "string",
                "default": "",
                "description": "Label selector filter (e.g. 'app=my-service')",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of pods to return",
            },
        },
        "required": [],
    }
    outputs = {
        "pods": "List of pods with phase, container statuses, and node assignment",
        "total": "Total number of pods returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "label_selector": "",
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        label_selector: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"pods": [], "total": 0})
        with client:
            result = client.list_pods(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), pods=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "pods": result["pods"],
                "total": result["total"],
            }


kubernetes_list_pods = KubernetesListPodsTool()


class KubernetesGetPodLogsTool(BaseTool):
    """Fetch recent log lines from a Kubernetes pod container."""

    name = "kubernetes_get_pod_logs"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Fetch recent log lines from one Kubernetes pod (optionally one container). "
        "Require the exact pod_name from kubernetes_list_pods; do not guess names."
    )
    use_cases = [
        "Reading application error logs from a crashing or misbehaving pod",
        "Diagnosing startup failures and misconfigurations via container logs",
        "Collecting evidence of OOM kills, panics, or stack traces",
    ]
    anti_examples = [
        "Do not call before kubernetes_list_pods when the pod name is unknown.",
        "Do not use for CloudWatch or Datadog log search — wrong backend.",
        "Do not use to exec into a pod or mutate cluster state.",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["pod_name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "pod_name": {
                "type": "string",
                "description": "Exact pod name (from kubernetes_list_pods)",
            },
            "container": {
                "type": "string",
                "default": "",
                "description": "Container name (required for multi-container pods)",
            },
            "tail_lines": {
                "type": "integer",
                "default": 100,
                "description": "Number of log lines to return from the end of the log",
            },
        },
        "required": ["pod_name"],
    }
    outputs = {
        "lines": "Log lines from the container",
        "total": "Number of log lines returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "pod_name": k8s.get("pod_name", ""),
            "container": k8s.get("container", ""),
            "tail_lines": 100,
        }

    def run(
        self,
        pod_name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        container: str = "",
        tail_lines: int = 100,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not pod_name:
            return tool_unavailable(
                "kubernetes",
                "pod_name is required; call kubernetes_list_pods first to find the pod name.",
                lines=[],
                total=0,
            )
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"lines": [], "total": 0})
        with client:
            result = client.get_pod_logs(
                namespace=namespace, pod_name=pod_name, container=container, tail_lines=tail_lines
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), lines=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "pod_name": pod_name,
                "namespace": namespace,
                "container": result.get("container"),
                "lines": result["lines"],
                "total": result["total"],
            }


kubernetes_get_pod_logs = KubernetesGetPodLogsTool()


class KubernetesListDeploymentsTool(BaseTool):
    """List Kubernetes deployments and their replica status."""

    name = "kubernetes_list_deployments"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List deployments in a Kubernetes namespace with their desired, ready, "
        "available, and unavailable replica counts. Use to detect degraded rollouts."
    )
    use_cases = [
        "Checking whether a deployment has unavailable replicas after a rollout",
        "Verifying deployment replica health across a namespace",
        "Identifying deployments stuck in a partial-rollout state",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of deployments to return",
            },
        },
        "required": [],
    }
    outputs = {
        "deployments": "List of deployments with desired/ready/available/unavailable replica counts",
        "total": "Total number of deployments returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"deployments": [], "total": 0})
        with client:
            result = client.list_deployments(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), deployments=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "deployments": result["deployments"],
                "total": result["total"],
            }


kubernetes_list_deployments = KubernetesListDeploymentsTool()


class KubernetesGetEventsTool(BaseTool):
    """List Kubernetes events for a namespace to diagnose crash loops and scheduling failures."""

    name = "kubernetes_get_events"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List Kubernetes events for a namespace. Events capture crash loops, "
        "OOM kills, image pull failures, and scheduling issues. "
        "Use field_selector to scope events to a specific pod or deployment."
    )
    use_cases = [
        "Diagnosing crash-loop back-off by reading Warning events for a pod",
        "Detecting OOM kills and image pull failures from cluster events",
        "Understanding scheduling failures (Insufficient CPU/Memory)",
        "Correlating event timestamps with incident timeline",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "field_selector": {
                "type": "string",
                "default": "",
                "description": (
                    "Field selector to filter events "
                    "(e.g. 'involvedObject.name=my-pod,type=Warning')"
                ),
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of events to return",
            },
        },
        "required": [],
    }
    outputs = {
        "events": "List of events with reason, message, involved object, and timestamps",
        "total": "Total number of events returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "field_selector": "",
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        field_selector: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"events": [], "total": 0})
        with client:
            result = client.get_events(
                namespace=namespace, field_selector=field_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), events=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "events": result["events"],
                "total": result["total"],
            }


kubernetes_get_events = KubernetesGetEventsTool()


class KubernetesDescribePodTool(BaseTool):
    """Fetch full spec, status, and container states for a single Kubernetes pod."""

    name = "kubernetes_describe_pod"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Fetch the full spec and status for a single pod: containers, images, resource "
        "requests/limits, environment variables (values redacted, keys only), volume "
        "mounts, conditions, container states, and owner references. Use when list_pods "
        "shows a problem and you need deeper detail on one pod. Preferred over "
        "kubernetes_get_resource for pods specifically — both redact env values "
        "identically, but this tool returns a curated, investigation-shaped view "
        "(container states, owner references) rather than the raw API object. For any "
        "resource type other than pod, use kubernetes_get_resource instead."
    )
    use_cases = [
        "Inspecting container image versions and resource limits on a specific pod",
        "Diagnosing why a pod is stuck in Pending or Init state via detailed conditions",
        "Identifying owner (Deployment, StatefulSet, Job) of a pod",
        "Checking environment variable names (keys only) injected into a container — values are redacted",
    ]
    anti_examples = [
        "Fetching a non-pod resource such as a deployment, service, or node (use "
        + "kubernetes_get_resource)",
        "Listing many pods at once (use kubernetes_list_pods)",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["pod_name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "pod_name": {"type": "string", "description": "Name of the pod to describe"},
        },
        "required": ["pod_name"],
    }
    outputs = {
        "spec": "Pod spec including containers, volumes, node selector, and tolerations",
        "status": "Pod status including phase, conditions, and per-container states",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "pod_name": k8s.get("pod_name", ""),
        }

    def run(
        self,
        pod_name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"spec": {}, "status": {}})
        with client:
            result = client.describe_pod(namespace=namespace, pod_name=pod_name)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), spec={}, status={}
                )
            return {
                "source": "kubernetes",
                "available": True,
                **{k: v for k, v in result.items() if k != "success"},
            }


kubernetes_describe_pod = KubernetesDescribePodTool()


class KubernetesListNodesTool(BaseTool):
    """List Kubernetes cluster nodes with conditions and capacity."""

    name = "kubernetes_list_nodes"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List all nodes in the Kubernetes cluster with their readiness conditions, "
        "CPU/memory capacity and allocatable resources, and taints. "
        "Use to diagnose node pressure, NotReady nodes, or scheduling issues."
    )
    use_cases = [
        "Finding nodes in NotReady or MemoryPressure/DiskPressure condition",
        "Checking available allocatable CPU and memory across nodes",
        "Identifying nodes with taints that prevent pod scheduling",
        "Correlating pod scheduling failures with node capacity",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context"]
    input_schema = {
        "type": "object",
        "properties": {
            "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
            "kubeconfig_path": {
                "type": "string",
                "default": "",
                "description": "Path to kubeconfig file (alternative to kubeconfig)",
            },
            "context": {
                "type": "string",
                "default": "",
                "description": "Kubeconfig context to use",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of nodes to return",
            },
        },
        "required": [],
    }
    outputs = {
        "nodes": "List of nodes with conditions, capacity, allocatable resources, and taints",
        "total": "Total number of nodes returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": "default",
                }
            }
        )
        if client is None:
            return _missing_client_error({"nodes": [], "total": 0})
        with client:
            result = client.list_nodes(limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), nodes=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "nodes": result["nodes"],
                "total": result["total"],
            }


kubernetes_list_nodes = KubernetesListNodesTool()


class KubernetesListServicesTool(BaseTool):
    """List Kubernetes services with their type, ports, and selector."""

    name = "kubernetes_list_services"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List services in a Kubernetes namespace with their type (ClusterIP/NodePort/LoadBalancer), "
        "clusterIP, external IPs, port mappings, and pod selector. "
        "Use to diagnose connectivity issues or verify service routing."
    )
    use_cases = [
        "Checking which pods a service routes to via its selector",
        "Verifying LoadBalancer external IP assignment",
        "Diagnosing port mismatches between services and pods",
        "Finding services exposed via NodePort for debugging",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "label_selector": {
                "type": "string",
                "default": "",
                "description": "Label selector filter (e.g. 'app=my-service')",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of services to return",
            },
        },
        "required": [],
    }
    outputs = {
        "services": "List of services with type, clusterIP, ports, and selector",
        "total": "Total number of services returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "label_selector": "",
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        label_selector: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"services": [], "total": 0})
        with client:
            result = client.list_services(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), services=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "services": result["services"],
                "total": result["total"],
            }


kubernetes_list_services = KubernetesListServicesTool()


class KubernetesListStatefulSetsTool(BaseTool):
    """List Kubernetes StatefulSets with replica status."""

    name = "kubernetes_list_statefulsets"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List StatefulSets in a Kubernetes namespace with desired, ready, current, "
        "and updated replica counts. Use to detect degraded or stalled StatefulSet rollouts."
    )
    use_cases = [
        "Checking whether a StatefulSet has unavailable replicas",
        "Diagnosing stuck StatefulSet rolling updates",
        "Verifying database or stateful service replica health",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of StatefulSets to return",
            },
        },
        "required": [],
    }
    outputs = {
        "statefulsets": "List of StatefulSets with desired/ready/current/updated replica counts",
        "total": "Total number of StatefulSets returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"statefulsets": [], "total": 0})
        with client:
            result = client.list_statefulsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), statefulsets=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "statefulsets": result["statefulsets"],
                "total": result["total"],
            }


kubernetes_list_statefulsets = KubernetesListStatefulSetsTool()


class KubernetesListDaemonSetsTool(BaseTool):
    """List Kubernetes DaemonSets with desired/ready/available counts."""

    name = "kubernetes_list_daemonsets"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List DaemonSets in a Kubernetes namespace with desired, current, ready, "
        "up-to-date, and available counts per node. "
        "Use to diagnose node-agent or logging/monitoring DaemonSet issues."
    )
    use_cases = [
        "Checking whether a DaemonSet is running on all expected nodes",
        "Diagnosing nodes where a DaemonSet pod is not scheduled or not ready",
        "Verifying a DaemonSet update has rolled out to all nodes",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of DaemonSets to return",
            },
        },
        "required": [],
    }
    outputs = {
        "daemonsets": "List of DaemonSets with desired/current/ready/up_to_date/available counts",
        "total": "Total number of DaemonSets returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"daemonsets": [], "total": 0})
        with client:
            result = client.list_daemonsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), daemonsets=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "daemonsets": result["daemonsets"],
                "total": result["total"],
            }


kubernetes_list_daemonsets = KubernetesListDaemonSetsTool()


class KubernetesListIngressesTool(BaseTool):
    """List Kubernetes Ingress resources with routing rules and TLS config."""

    name = "kubernetes_list_ingresses"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List Ingress resources in a Kubernetes namespace with their host rules, "
        "path-to-service mappings, TLS configuration, and load balancer status. "
        "Use to diagnose HTTP routing misconfigurations."
    )
    use_cases = [
        "Checking which service an ingress path routes to",
        "Verifying TLS certificate secret names and covered hosts",
        "Finding load balancer IPs or hostnames assigned to an ingress",
        "Diagnosing 404 or routing issues in HTTP-based services",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of Ingresses to return",
            },
        },
        "required": [],
    }
    outputs = {
        "ingresses": "List of Ingresses with host rules, path-service mappings, TLS, and LB status",
        "total": "Total number of Ingresses returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"ingresses": [], "total": 0})
        with client:
            result = client.list_ingresses(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), ingresses=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "ingresses": result["ingresses"],
                "total": result["total"],
            }


kubernetes_list_ingresses = KubernetesListIngressesTool()


class KubernetesListConfigMapsTool(BaseTool):
    """List Kubernetes ConfigMaps with their key-value data."""

    name = "kubernetes_list_configmaps"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "List ConfigMaps in a Kubernetes namespace with their full key-value data. "
        "Use to inspect application configuration, verify environment variable sources, "
        "or check for misconfigured settings."
    )
    use_cases = [
        "Inspecting application configuration values injected via ConfigMap",
        "Verifying a ConfigMap has the expected keys and values after a deploy",
        "Diagnosing misconfigured endpoints, feature flags, or environment settings",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of ConfigMaps to return",
            },
        },
        "required": [],
    }
    outputs = {
        "configmaps": "List of ConfigMaps with their data key-value pairs",
        "total": "Total number of ConfigMaps returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error({"configmaps": [], "total": 0})
        with client:
            result = client.list_configmaps(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), configmaps=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "configmaps": result["configmaps"],
                "total": result["total"],
            }


kubernetes_list_configmaps = KubernetesListConfigMapsTool()


class KubernetesGetResourceTool(BaseTool):
    """Fetch a single named Kubernetes resource by type and name."""

    name = "kubernetes_get_resource"
    source = "kubernetes"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    description = (
        "Fetch the raw spec and status of a single named Kubernetes resource, "
        "equivalent to `kubectl get <type> <name> -o json`. Supports: pod, deployment, "
        "statefulset, daemonset, service, ingress, configmap, replicaset, "
        "persistentvolumeclaim (pvc), and node. Env variable values are redacted (keys "
        "only) for pod and workload types (deployment, statefulset, daemonset, "
        "replicaset), same as kubernetes_describe_pod. For POD detail specifically, "
        "prefer kubernetes_describe_pod instead — it returns the same redacted data in "
        "a curated, investigation-shaped view (container states, owner references) "
        "rather than the raw API object. The namespace field is ignored for "
        "cluster-scoped types like node."
    )
    use_cases = [
        "Fetching the full YAML-equivalent of any non-pod named resource for deep inspection",
        "Reading a specific deployment's full spec including strategy and selector",
        "Inspecting a PVC's storage class, capacity, and bound status",
        "Getting the full node spec to check kubelet version and OS image",
    ]
    anti_examples = [
        "Inspecting a pod's containers, conditions, or env var keys (use "
        + "kubernetes_describe_pod — same detail, redacts env values)",
        "Listing multiple resources or filtering by label/field selector (use the "
        + "matching kubernetes_list_* tool instead)",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["resource_type", "name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "namespace"]
    input_schema = {
        "type": "object",
        "properties": {
            **_SHARED_KUBECONFIG_PROPS,
            "resource_type": {
                "type": "string",
                "enum": _RESOURCE_TYPE_ENUM,
                "description": "Kubernetes resource type to fetch.",
            },
            "name": {
                "type": "string",
                "description": "Name of the resource to fetch",
            },
        },
        "required": ["resource_type", "name"],
    }
    outputs = {
        "resource": "Full resource object as a dict (equivalent to kubectl get -o json)",
        "resource_type": "The resource type that was fetched",
        "name": "The resource name that was fetched",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "resource_type": k8s.get("resource_type", ""),
            "name": k8s.get("name", ""),
        }

    def run(
        self,
        resource_type: str,
        name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _missing_client_error(
                {"resource": {}, "resource_type": resource_type, "name": name}
            )
        with client:
            result = client.get_resource(
                resource_type=resource_type, name=name, namespace=namespace
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes",
                    result.get("error", "unknown error"),
                    resource={},
                    resource_type=resource_type,
                    name=name,
                )
            return {
                "source": "kubernetes",
                "available": True,
                "resource_type": result["resource_type"],
                "name": result["name"],
                "resource": result["resource"],
            }


kubernetes_get_resource = KubernetesGetResourceTool()
