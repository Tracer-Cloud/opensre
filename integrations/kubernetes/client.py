"""Kubernetes API client.

Supports two auth paths:
- File path (``kubeconfig_path``): uses ``load_kube_config`` for a single file.
  For colon-separated multi-file values (e.g. ``~/.kube/config:~/.kube/dev``),
  ``config_file=`` is omitted so the SDK reads ``KUBECONFIG`` from the
  environment and performs the merge natively.
- Inline YAML (``kubeconfig``): uses ``load_kube_config_from_dict`` for configs
  stored as strings (e.g. from a secrets manager).

``kubeconfig_path`` takes precedence when both are present.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from infrastructure.observability.errors.service import capture_service_error
from integrations.config_models import KubernetesIntegrationConfig
from integrations.kubernetes.resources import RESOURCE_DISPATCH
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

_DEFAULT_TAIL_LINES = 100
_DEFAULT_LIMIT = 50


# Resource types that carry env vars and require value redaction before returning to the LLM.
_WORKLOAD_TYPES: frozenset[str] = frozenset(
    {
        "pod",
        "pods",
        "deployment",
        "deployments",
        "statefulset",
        "statefulsets",
        "daemonset",
        "daemonsets",
        "replicaset",
        "replicasets",
    }
)

# kubectl writes the full applied manifest -- including literal env var
# values -- into this annotation on `kubectl apply`. It must be stripped
# wherever annotations are returned, or it silently reintroduces the
# credentials that _redact_env_values and the env-name-only projection in
# describe_pod were written to remove.
_LAST_APPLIED_CONFIG_ANNOTATION = "kubectl.kubernetes.io/last-applied-configuration"


def _redact_annotations(annotations: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``annotations`` with the last-applied-configuration key removed."""
    return {k: v for k, v in (annotations or {}).items() if k != _LAST_APPLIED_CONFIG_ANNOTATION}


def _redact_env_values(resource_dict: dict[str, Any]) -> None:
    """Strip env var values from a serialized workload dict in-place.

    sanitize_for_serialization() returns the full Kubernetes object JSON
    including env[].value for literal env vars. Removing values keeps
    credential redaction consistent with describe_pod (returns only key
    names) and prevents tokens/DB URLs/API keys from reaching the LLM.

    Handles two layouts:
    - Pod: spec.containers[]/initContainers[]/ephemeralContainers[]
    - Workload controllers (Deployment/StatefulSet/DaemonSet/ReplicaSet):
      spec.template.spec.containers[]/initContainers[]/ephemeralContainers[]
    """

    def _strip_env(spec: dict[str, Any]) -> None:
        for key in ("containers", "initContainers", "ephemeralContainers"):
            for container in spec.get(key) or []:
                for env_entry in container.get("env") or []:
                    env_entry.pop("value", None)

    spec = resource_dict.get("spec") or {}
    _strip_env(spec)
    # Pod template inside workload controllers
    template_spec = ((spec.get("template") or {}).get("spec")) or {}
    if template_spec:
        _strip_env(template_spec)


class KubernetesClient:
    """Kubernetes API client built from a kubeconfig file path or inline YAML."""

    def __init__(self, config: KubernetesIntegrationConfig) -> None:
        self.config = config
        self._core_v1: k8s_client.CoreV1Api | None = None
        self._apps_v1: k8s_client.AppsV1Api | None = None
        self._networking_v1: k8s_client.NetworkingV1Api | None = None
        self._api_client: k8s_client.ApiClient | None = None

    def _build_clients(
        self,
    ) -> tuple[k8s_client.CoreV1Api, k8s_client.AppsV1Api, k8s_client.NetworkingV1Api]:
        api_config = k8s_client.Configuration()
        context = self.config.context or None
        if self.config.kubeconfig_path:
            # config_file= is forwarded verbatim to KubeConfigMerger, which
            # already splits on the OS path separator (":" on non-Windows) and
            # merges each file itself — the same mechanism the SDK uses when
            # reading the KUBECONFIG env var. Passing the configured value
            # directly (rather than falling back to config_file=None) keeps
            # DB-stored integrations from silently reading the process
            # environment's KUBECONFIG instead of the configured paths.
            k8s_config.load_kube_config(
                config_file=self.config.kubeconfig_path,
                client_configuration=api_config,
                context=context,
            )
        else:
            kubeconfig_dict = yaml.safe_load(self.config.kubeconfig)
            k8s_config.load_kube_config_from_dict(
                config_dict=kubeconfig_dict,
                client_configuration=api_config,
                context=context,
            )
        api_client = k8s_client.ApiClient(api_config)
        self._api_client = api_client
        return (
            k8s_client.CoreV1Api(api_client),
            k8s_client.AppsV1Api(api_client),
            k8s_client.NetworkingV1Api(api_client),
        )

    def _get_clients(
        self,
    ) -> tuple[k8s_client.CoreV1Api, k8s_client.AppsV1Api, k8s_client.NetworkingV1Api]:
        if self._core_v1 is None or self._apps_v1 is None or self._networking_v1 is None:
            self._core_v1, self._apps_v1, self._networking_v1 = self._build_clients()
        return self._core_v1, self._apps_v1, self._networking_v1

    def close(self) -> None:
        """Close the underlying ApiClient connection pool."""
        if self._api_client is not None:
            self._api_client.close()

    def __enter__(self) -> KubernetesClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def probe_access(self) -> ProbeResult:
        """Validate Kubernetes connectivity against the configured namespace.

        Uses list_namespaced_pod (namespace-scoped) rather than list_namespace
        (cluster-wide). Cluster-wide calls require a ClusterRole binding which
        is unavailable on GKE Workload Identity, AKS Managed Identity, on-prem
        Role bindings, and k3s restricted configs — all typical targets for this
        integration. Every investigation tool is namespace-scoped, so a
        namespace-scoped probe accurately reflects real access.
        """
        if not self.is_configured:
            return ProbeResult.missing("Missing kubeconfig or kubeconfig_path.")
        try:
            namespace = self.config.namespace or "default"
            core_v1, _, _ = self._get_clients()
            pod_list = core_v1.list_namespaced_pod(namespace=namespace, limit=1)
            pod_count = len(pod_list.items or [])
            return ProbeResult.passed(
                f"Connected to Kubernetes cluster; namespace '{namespace}' accessible "
                f"({pod_count} pod(s) visible).",
                namespace=namespace,
            )
        except ConfigException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="probe_access"
            )
            return ProbeResult.failed(str(exc))
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="probe_access"
            )
            return ProbeResult.failed(f"Kubernetes API error {exc.status}: {str(exc.reason)}")
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="probe_access"
            )
            return ProbeResult.failed(str(exc))

    def list_pods(
        self,
        namespace: str = "default",
        label_selector: str = "",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List pods in a namespace with optional label selector filter."""
        try:
            core_v1, _, _ = self._get_clients()
            kwargs: dict[str, Any] = {"limit": limit}
            if label_selector:
                kwargs["label_selector"] = label_selector
            pod_list = core_v1.list_namespaced_pod(namespace=namespace, **kwargs)
            pods = []
            for pod in pod_list.items or []:
                meta = pod.metadata
                status = pod.status
                containers = [
                    {"name": c.name, "ready": c.ready, "restart_count": c.restart_count}
                    for c in (status.container_statuses or [])
                ]
                pods.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "phase": status.phase,
                        "conditions": [
                            {"type": c.type, "status": c.status} for c in (status.conditions or [])
                        ],
                        "containers": containers,
                        "node_name": spec.node_name if (spec := pod.spec) else None,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                    }
                )
            return {"success": True, "pods": pods, "total": len(pods)}
        except ApiException as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_pods")
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
            }
        except Exception as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_pods")
            return {"success": False, "error": str(exc)}

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        container: str = "",
        tail_lines: int = _DEFAULT_TAIL_LINES,
    ) -> dict[str, Any]:
        """Fetch recent log lines from a pod container."""
        try:
            core_v1, _, _ = self._get_clients()
            kwargs: dict[str, Any] = {"tail_lines": tail_lines}
            if container:
                kwargs["container"] = container
            # kubernetes-client's generated deserializer declares this endpoint's
            # response type as ``str``, but a plain-text log body is never valid
            # JSON, so its fallback path calls ``str()`` directly on the raw response
            # bytes instead of decoding them -- producing the literal text
            # "b'...'" rather than the actual log content (confirmed against
            # kubernetes-client 36.0.3). ``_preload_content=False`` returns the raw
            # urllib3 response so we can decode it correctly ourselves.
            raw = core_v1.read_namespaced_pod_log(
                name=pod_name, namespace=namespace, _preload_content=False, **kwargs
            )
            logs = raw.data.decode("utf-8", errors="replace")
            lines = logs.splitlines() if logs else []
            return {
                "success": True,
                "pod_name": pod_name,
                "namespace": namespace,
                "container": container or None,
                "lines": lines,
                "total": len(lines),
            }
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_pod_logs"
            )
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_pod_logs"
            )
            return {"success": False, "error": str(exc)}

    def list_deployments(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List deployments and their replica status in a namespace."""
        try:
            _, apps_v1, _ = self._get_clients()
            dep_list = apps_v1.list_namespaced_deployment(namespace=namespace, limit=limit)
            deployments = []
            for dep in dep_list.items or []:
                meta = dep.metadata
                spec = dep.spec
                status = dep.status
                deployments.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "desired": spec.replicas if spec else None,
                        "ready": status.ready_replicas,
                        "available": status.available_replicas,
                        "unavailable": status.unavailable_replicas,
                        "updated": status.updated_replicas,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                    }
                )
            return {"success": True, "deployments": deployments, "total": len(deployments)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_deployments"
            )
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_deployments"
            )
            return {"success": False, "error": str(exc)}

    def get_events(
        self,
        namespace: str = "default",
        field_selector: str = "",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Kubernetes events for a namespace, useful for crash loops and OOM kills."""
        try:
            core_v1, _, _ = self._get_clients()
            kwargs: dict[str, Any] = {"limit": limit}
            if field_selector:
                kwargs["field_selector"] = field_selector
            event_list = core_v1.list_namespaced_event(namespace=namespace, **kwargs)
            events = []
            for ev in event_list.items or []:
                meta = ev.metadata
                events.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "reason": ev.reason,
                        "message": ev.message,
                        "type": ev.type,
                        "count": ev.count,
                        "involved_object": {
                            "kind": ev.involved_object.kind,
                            "name": ev.involved_object.name,
                            "namespace": ev.involved_object.namespace,
                        }
                        if ev.involved_object
                        else {},
                        "first_timestamp": (
                            ev.first_timestamp.isoformat() if ev.first_timestamp else None
                        ),
                        "last_timestamp": (
                            ev.last_timestamp.isoformat() if ev.last_timestamp else None
                        ),
                    }
                )
            return {"success": True, "events": events, "total": len(events)}
        except ApiException as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="get_events")
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
            }
        except Exception as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="get_events")
            return {"success": False, "error": str(exc)}

    def describe_pod(self, namespace: str, pod_name: str) -> dict[str, Any]:
        """Return full pod spec, status, and container states for a single pod."""
        try:
            core_v1, _, _ = self._get_clients()
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            meta = pod.metadata
            spec = pod.spec
            status = pod.status

            def _container_spec(c: Any) -> dict[str, Any]:
                return {
                    "name": c.name,
                    "image": c.image,
                    "command": list(c.command) if c.command else [],
                    "args": list(c.args) if c.args else [],
                    "ports": [
                        {"container_port": p.container_port, "protocol": p.protocol}
                        for p in (c.ports or [])
                    ],
                    "resources": {
                        "requests": dict(c.resources.requests or {}) if c.resources else {},
                        "limits": dict(c.resources.limits or {}) if c.resources else {},
                    },
                    # Return only env var names, not values, regardless of source
                    # (literal value vs. valueFrom secretKeyRef/configMapKeyRef).
                    # Literal values may contain credentials (tokens, DB URLs, API
                    # keys) that must not be forwarded to the LLM or stored in
                    # investigation reports.
                    "env": [e.name for e in (c.env or [])],
                }

            def _container_status(cs: Any) -> dict[str, Any]:
                state: dict[str, Any] = {}
                if cs.state:
                    if cs.state.running:
                        state = {
                            "running": {
                                "started_at": cs.state.running.started_at.isoformat()
                                if cs.state.running.started_at
                                else None
                            }
                        }
                    elif cs.state.waiting:
                        state = {
                            "waiting": {
                                "reason": cs.state.waiting.reason,
                                "message": cs.state.waiting.message,
                            }
                        }
                    elif cs.state.terminated:
                        state = {
                            "terminated": {
                                "reason": cs.state.terminated.reason,
                                "exit_code": cs.state.terminated.exit_code,
                            }
                        }
                return {
                    "name": cs.name,
                    "image": cs.image,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": state,
                }

            return {
                "success": True,
                "name": meta.name,
                "namespace": meta.namespace,
                "labels": dict(meta.labels or {}),
                "annotations": _redact_annotations(meta.annotations),
                "creation_timestamp": meta.creation_timestamp.isoformat()
                if meta.creation_timestamp
                else None,
                "owner_references": [
                    {"kind": o.kind, "name": o.name} for o in (meta.owner_references or [])
                ],
                "spec": {
                    "node_name": spec.node_name if spec else None,
                    "service_account_name": spec.service_account_name if spec else None,
                    "node_selector": dict(spec.node_selector or {}) if spec else {},
                    "containers": [_container_spec(c) for c in (spec.containers or [])]
                    if spec
                    else [],
                    "init_containers": [_container_spec(c) for c in (spec.init_containers or [])]
                    if spec
                    else [],
                    "volumes": [{"name": v.name} for v in (spec.volumes or [])] if spec else [],
                },
                "status": {
                    "phase": status.phase if status else None,
                    "host_ip": status.host_ip if status else None,
                    "pod_ip": status.pod_ip if status else None,
                    "conditions": [
                        {"type": c.type, "status": c.status, "reason": c.reason}
                        for c in (status.conditions or [])
                    ]
                    if status
                    else [],
                    "container_statuses": [
                        _container_status(cs) for cs in (status.container_statuses or [])
                    ]
                    if status
                    else [],
                    "init_container_statuses": [
                        _container_status(cs) for cs in (status.init_container_statuses or [])
                    ]
                    if status
                    else [],
                },
            }
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="describe_pod"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="describe_pod"
            )
            return {"success": False, "error": str(exc)}

    def list_nodes(self, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
        """List cluster nodes with conditions and capacity."""
        try:
            core_v1, _, _ = self._get_clients()
            node_list = core_v1.list_node(limit=limit)
            nodes = []
            for node in node_list.items or []:
                meta = node.metadata
                status = node.status
                spec = node.spec
                nodes.append(
                    {
                        "name": meta.name,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "taints": [
                            {"key": t.key, "effect": t.effect, "value": t.value}
                            for t in (spec.taints or [])
                        ]
                        if spec
                        else [],
                        "conditions": [
                            {
                                "type": c.type,
                                "status": c.status,
                                "reason": c.reason,
                                "message": c.message,
                            }
                            for c in (status.conditions or [])
                        ]
                        if status
                        else [],
                        "capacity": dict(status.capacity or {}) if status else {},
                        "allocatable": dict(status.allocatable or {}) if status else {},
                    }
                )
            return {"success": True, "nodes": nodes, "total": len(nodes)}
        except ApiException as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_nodes")
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_nodes")
            return {"success": False, "error": str(exc)}

    def list_services(
        self,
        namespace: str = "default",
        label_selector: str = "",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List services with their type, clusterIP, ports, and selector."""
        try:
            core_v1, _, _ = self._get_clients()
            kwargs: dict[str, Any] = {"limit": limit}
            if label_selector:
                kwargs["label_selector"] = label_selector
            svc_list = core_v1.list_namespaced_service(namespace=namespace, **kwargs)
            services = []
            for svc in svc_list.items or []:
                meta = svc.metadata
                spec = svc.spec
                services.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "type": spec.type if spec else None,
                        "cluster_ip": spec.cluster_ip if spec else None,
                        "external_ips": list(spec.external_ips or []) if spec else [],
                        "selector": dict(spec.selector or {}) if spec else {},
                        "ports": [
                            {
                                "name": p.name,
                                "port": p.port,
                                "target_port": str(p.target_port) if p.target_port else None,
                                "protocol": p.protocol,
                                "node_port": p.node_port,
                            }
                            for p in (spec.ports or [])
                        ]
                        if spec
                        else [],
                    }
                )
            return {"success": True, "services": services, "total": len(services)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_services"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_services"
            )
            return {"success": False, "error": str(exc)}

    def list_statefulsets(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List StatefulSets with replica status."""
        try:
            _, apps_v1, _ = self._get_clients()
            sts_list = apps_v1.list_namespaced_stateful_set(namespace=namespace, limit=limit)
            statefulsets = []
            for sts in sts_list.items or []:
                meta = sts.metadata
                spec = sts.spec
                status = sts.status
                statefulsets.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "desired": spec.replicas if spec else None,
                        "ready": status.ready_replicas if status else None,
                        "current": status.current_replicas if status else None,
                        "updated": status.updated_replicas if status else None,
                    }
                )
            return {"success": True, "statefulsets": statefulsets, "total": len(statefulsets)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_statefulsets"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_statefulsets"
            )
            return {"success": False, "error": str(exc)}

    def list_daemonsets(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List DaemonSets with desired/ready/available counts."""
        try:
            _, apps_v1, _ = self._get_clients()
            ds_list = apps_v1.list_namespaced_daemon_set(namespace=namespace, limit=limit)
            daemonsets = []
            for ds in ds_list.items or []:
                meta = ds.metadata
                status = ds.status
                daemonsets.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "desired": status.desired_number_scheduled if status else None,
                        "current": status.current_number_scheduled if status else None,
                        "ready": status.number_ready if status else None,
                        "up_to_date": status.updated_number_scheduled if status else None,
                        "available": status.number_available if status else None,
                    }
                )
            return {"success": True, "daemonsets": daemonsets, "total": len(daemonsets)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_daemonsets"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_daemonsets"
            )
            return {"success": False, "error": str(exc)}

    def list_ingresses(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Ingress resources with rules, hosts, and TLS configuration."""
        try:
            _, _, networking_v1 = self._get_clients()
            ing_list = networking_v1.list_namespaced_ingress(namespace=namespace, limit=limit)
            ingresses = []
            for ing in ing_list.items or []:
                meta = ing.metadata
                spec = ing.spec
                status = ing.status
                rules = []
                for rule in (spec.rules or []) if spec else []:
                    paths = []
                    if rule.http:
                        for path in rule.http.paths or []:
                            backend = path.backend
                            svc_name = None
                            svc_port = None
                            if backend and backend.service:
                                svc_name = backend.service.name
                                svc_port = (
                                    backend.service.port.number if backend.service.port else None
                                )
                            paths.append(
                                {
                                    "path": path.path,
                                    "path_type": path.path_type,
                                    "service_name": svc_name,
                                    "service_port": svc_port,
                                }
                            )
                    rules.append({"host": rule.host, "paths": paths})
                lb_ingress = []
                if status and status.load_balancer and status.load_balancer.ingress:
                    lb_ingress = [
                        {"ip": i.ip, "hostname": i.hostname} for i in status.load_balancer.ingress
                    ]
                ingresses.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "ingress_class": spec.ingress_class_name if spec else None,
                        "rules": rules,
                        "tls": [
                            {"hosts": list(t.hosts or []), "secret_name": t.secret_name}
                            for t in (spec.tls or [])
                        ]
                        if spec
                        else [],
                        "load_balancer": lb_ingress,
                    }
                )
            return {"success": True, "ingresses": ingresses, "total": len(ingresses)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_ingresses"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_ingresses"
            )
            return {"success": False, "error": str(exc)}

    def list_configmaps(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List ConfigMaps with their key-value data."""
        try:
            core_v1, _, _ = self._get_clients()
            cm_list = core_v1.list_namespaced_config_map(namespace=namespace, limit=limit)
            configmaps = []
            for cm in cm_list.items or []:
                meta = cm.metadata
                configmaps.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "data": dict(cm.data or {}),
                        "data_keys": list((cm.data or {}).keys()),
                    }
                )
            return {"success": True, "configmaps": configmaps, "total": len(configmaps)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_configmaps"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_configmaps"
            )
            return {"success": False, "error": str(exc)}

    def get_resource(
        self,
        resource_type: str,
        name: str,
        namespace: str = "default",
    ) -> dict[str, Any]:
        """Fetch a single named Kubernetes resource by type and name."""
        rt = (
            resource_type.lower().rstrip("s")
            if resource_type.lower() not in RESOURCE_DISPATCH
            else resource_type.lower()
        )
        # normalize plural/singular
        entry = RESOURCE_DISPATCH.get(rt) or RESOURCE_DISPATCH.get(resource_type.lower())
        if entry is None:
            supported = sorted({k.rstrip("s") for k in RESOURCE_DISPATCH})
            return {
                "success": False,
                "error": (
                    f"Unsupported resource_type '{resource_type}'. Supported types: {supported}"
                ),
            }
        api_key, method_name, is_cluster_scoped = entry
        try:
            core_v1, apps_v1, networking_v1 = self._get_clients()
            api_map: dict[str, Any] = {
                "core": core_v1,
                "apps": apps_v1,
                "networking": networking_v1,
            }
            api = api_map[api_key]
            method = getattr(api, method_name)
            if is_cluster_scoped:
                obj = method(name=name)
            else:
                obj = method(name=name, namespace=namespace)
            assert self._api_client is not None  # always set by _build_clients()
            resource_dict: dict[str, Any] = self._api_client.sanitize_for_serialization(obj)
            # Redact env var values from pod and workload resources to prevent
            # credential leakage to the LLM. Workload controllers (Deployment,
            # StatefulSet, DaemonSet, ReplicaSet) embed a pod template that also
            # carries env vars. Consistent with describe_pod (keys only).
            if rt in _WORKLOAD_TYPES:
                _redact_env_values(resource_dict)
            metadata = resource_dict.get("metadata")
            if isinstance(metadata, dict):
                metadata["annotations"] = _redact_annotations(metadata.get("annotations"))
            return {
                "success": True,
                "resource_type": resource_type,
                "name": name,
                "resource": resource_dict,
            }
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_resource"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_resource"
            )
            return {"success": False, "error": str(exc)}
