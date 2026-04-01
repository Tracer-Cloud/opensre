"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging

from app.tools.clients.eks.eks_k8s_client import build_k8s_clients
from app.tools.tool_actions.base import BaseTool

logger = logging.getLogger(__name__)


def _eks_available(sources: dict) -> bool:
    return bool(sources.get("eks", {}).get("connection_verified"))


def _eks_creds(eks: dict) -> dict:
    return {
        "role_arn": eks["role_arn"],
        "external_id": eks.get("external_id", ""),
        "region": eks.get("region", "us-east-1"),
    }


class EKSPodLogsTool(BaseTool):
    """Fetch logs from a specific EKS pod."""

    name = "get_eks_pod_logs"
    source = "eks"
    description = "Fetch logs from a specific EKS pod."
    use_cases = [
        "Fetching crash logs from a specific pod",
        "Reviewing application output for a known failing pod",
    ]
    requires = ["cluster_name", "pod_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "pod_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "tail_lines": {"type": "integer", "default": 100},
        },
        "required": ["cluster_name", "namespace", "pod_name", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("pod_name"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_name": eks["cluster_name"],
            "namespace": eks.get("namespace", "default"),
            "pod_name": eks["pod_name"],
            **_eks_creds(eks),
        }

    def run(self, cluster_name: str, namespace: str, pod_name: str, role_arn: str, external_id: str = "", region: str = "us-east-1", tail_lines: int = 100, **_kwargs) -> dict:
        logger.info("[eks] get_eks_pod_logs cluster=%s ns=%s pod=%s", cluster_name, namespace, pod_name)
        try:
            core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
            logs = core_v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=tail_lines)
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name,
                "namespace": namespace, "pod_name": pod_name, "logs": logs, "error": None,
            }
        except Exception as e:
            logger.error("[eks] get_eks_pod_logs failed cluster=%s pod=%s error=%s", cluster_name, pod_name, e, exc_info=True)
            return {"source": "eks", "available": False, "pod_name": pod_name, "error": str(e)}


class EKSListPodsTool(BaseTool):
    """List all pods in a namespace with their status, phase, restart counts, and conditions."""

    name = "list_eks_pods"
    source = "eks"
    description = "List all pods in a namespace with their status, phase, restart counts, and conditions."
    use_cases = [
        "Discovering what pods exist before fetching logs",
        "Finding which pods are crashing, pending, or failed",
        "Checking restart counts for crash-looping containers",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' to scan all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return _eks_available(sources)

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_name": eks["cluster_name"],
            "namespace": eks.get("namespace") or "all",
            **_eks_creds(eks),
        }

    def run(self, cluster_name: str, namespace: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] list_eks_pods cluster=%s ns=%s", cluster_name, namespace)
        try:
            core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
            pod_list = core_v1.list_pod_for_all_namespaces() if namespace == "all" else core_v1.list_namespaced_pod(namespace=namespace)

            pods = []
            for pod in pod_list.items:
                containers = []
                for cs in (pod.status.container_statuses or []):
                    state = {}
                    if cs.state.running:
                        state = {"running": True, "started_at": str(cs.state.running.started_at)}
                    elif cs.state.waiting:
                        state = {"waiting": True, "reason": cs.state.waiting.reason, "message": cs.state.waiting.message}
                    elif cs.state.terminated:
                        state = {"terminated": True, "exit_code": cs.state.terminated.exit_code, "reason": cs.state.terminated.reason, "message": cs.state.terminated.message}
                    containers.append({"name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count, "state": state})
                conditions = [{"type": c.type, "status": c.status, "reason": c.reason, "message": c.message} for c in (pod.status.conditions or [])]
                pods.append({
                    "name": pod.metadata.name, "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase, "node_name": pod.spec.node_name,
                    "containers": containers, "conditions": conditions, "start_time": str(pod.status.start_time),
                })

            failing = [p for p in pods if p["phase"] not in ("Running", "Succeeded")]
            crashing = [p for p in pods if any(c["restart_count"] > 3 for c in p["containers"])]
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name, "namespace": namespace,
                "total_pods": len(pods), "pods": pods, "failing_pods": failing, "high_restart_pods": crashing, "error": None,
            }
        except Exception as e:
            logger.error("[eks] list_eks_pods FAILED: %s", e, exc_info=True)
            return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}


class EKSEventsTool(BaseTool):
    """Get Kubernetes Warning events in a namespace."""

    name = "get_eks_events"
    source = "eks"
    description = "Get Kubernetes Warning events in a namespace."
    use_cases = [
        "Finding OOMKilled, FailedScheduling, BackOff, Unhealthy, FailedMount events",
        "Understanding what Kubernetes reported during an incident",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' for all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_name": eks["cluster_name"],
            "namespace": eks.get("namespace", "default"),
            **_eks_creds(eks),
        }

    def run(self, cluster_name: str, namespace: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] get_eks_events cluster=%s ns=%s", cluster_name, namespace)
        try:
            core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
            event_list = core_v1.list_event_for_all_namespaces() if namespace == "all" else core_v1.list_namespaced_event(namespace=namespace)
            warning_events = [
                {
                    "namespace": e.metadata.namespace, "reason": e.reason, "message": e.message,
                    "type": e.type, "count": e.count,
                    "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}",
                    "first_time": str(e.first_timestamp), "last_time": str(e.last_timestamp),
                }
                for e in event_list.items if e.type == "Warning"
            ]
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name, "namespace": namespace,
                "warning_events": warning_events, "total_warning_count": len(warning_events), "error": None,
            }
        except Exception as e:
            logger.error("[eks] get_eks_events FAILED: %s", e, exc_info=True)
            return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}


class EKSDeploymentStatusTool(BaseTool):
    """Get EKS deployment rollout status — desired vs ready vs unavailable replicas."""

    name = "get_eks_deployment_status"
    source = "eks"
    description = "Get EKS deployment rollout status — desired vs ready vs unavailable replicas."
    use_cases = [
        "Checking if a deployment has unavailable replicas",
        "Verifying rollout status after a deployment change",
    ]
    requires = ["cluster_name", "deployment_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "deployment_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "deployment_name", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("deployment"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_name": eks["cluster_name"],
            "namespace": eks.get("namespace", "default"),
            "deployment_name": eks["deployment"],
            **_eks_creds(eks),
        }

    def run(self, cluster_name: str, namespace: str, deployment_name: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] get_eks_deployment_status cluster=%s ns=%s deployment=%s", cluster_name, namespace, deployment_name)
        try:
            _, apps_v1 = build_k8s_clients(cluster_name, role_arn, external_id, region)
            dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
            spec = dep.spec
            status = dep.status
            conditions = [{"type": c.type, "status": c.status, "reason": c.reason, "message": c.message} for c in (status.conditions or [])]
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name, "namespace": namespace,
                "deployment_name": deployment_name, "desired_replicas": spec.replicas,
                "ready_replicas": status.ready_replicas, "available_replicas": status.available_replicas,
                "unavailable_replicas": status.unavailable_replicas, "conditions": conditions, "error": None,
            }
        except Exception as e:
            logger.error("[eks] get_eks_deployment_status FAILED: %s", e, exc_info=True)
            return {"source": "eks", "available": False, "deployment_name": deployment_name, "error": str(e)}


class EKSListDeploymentsTool(BaseTool):
    """List all deployments in a namespace with replica counts and availability status."""

    name = "list_eks_deployments"
    source = "eks"
    description = "List all deployments in a namespace with replica counts and availability status."
    use_cases = [
        "Discovering what deployments exist and which are degraded/unavailable",
        "Scanning all namespaces for degraded deployments",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' for all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return _eks_available(sources)

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_name": eks["cluster_name"],
            "namespace": eks.get("namespace") or "all",
            **_eks_creds(eks),
        }

    def run(self, cluster_name: str, namespace: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] list_eks_deployments cluster=%s ns=%s", cluster_name, namespace)
        try:
            _, apps_v1 = build_k8s_clients(cluster_name, role_arn, external_id, region)
            dep_list = apps_v1.list_deployment_for_all_namespaces() if namespace == "all" else apps_v1.list_namespaced_deployment(namespace=namespace)
            deployments = []
            for dep in dep_list.items:
                status = dep.status
                desired = dep.spec.replicas or 0
                ready = status.ready_replicas or 0
                unavailable = status.unavailable_replicas or 0
                deployments.append({
                    "name": dep.metadata.name, "namespace": dep.metadata.namespace,
                    "desired": desired, "ready": ready,
                    "available": status.available_replicas or 0, "unavailable": unavailable,
                    "degraded": unavailable > 0 or ready < desired,
                })
            degraded = [d for d in deployments if d["degraded"]]
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name, "namespace": namespace,
                "total_deployments": len(deployments), "deployments": deployments,
                "degraded_deployments": degraded, "error": None,
            }
        except Exception as e:
            logger.error("[eks] list_eks_deployments FAILED: %s", e, exc_info=True)
            return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}


class EKSNodeHealthTool(BaseTool):
    """Get health status of all EKS nodes — conditions, capacity, allocatable, pod counts."""

    name = "get_eks_node_health"
    source = "eks"
    description = "Get health status of all EKS nodes — conditions, capacity, allocatable, pod counts."
    use_cases = [
        "Investigating when pods are unschedulable or nodes are NotReady",
        "Checking memory/disk pressure on nodes",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}

    def run(self, cluster_name: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] get_eks_node_health cluster=%s", cluster_name)
        try:
            core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
            nodes = core_v1.list_node()
            node_health = []
            for node in nodes.items:
                conditions = {c.type: c.status for c in (node.status.conditions or [])}
                capacity = node.status.capacity or {}
                allocatable = node.status.allocatable or {}
                addresses = {a.type: a.address for a in (node.status.addresses or [])}
                node_health.append({
                    "name": node.metadata.name, "internal_ip": addresses.get("InternalIP"),
                    "ready": conditions.get("Ready"), "memory_pressure": conditions.get("MemoryPressure"),
                    "disk_pressure": conditions.get("DiskPressure"), "pid_pressure": conditions.get("PIDPressure"),
                    "capacity_cpu": capacity.get("cpu"), "capacity_memory": capacity.get("memory"),
                    "allocatable_cpu": allocatable.get("cpu"), "allocatable_memory": allocatable.get("memory"),
                    "instance_type": node.metadata.labels.get("node.kubernetes.io/instance-type") if node.metadata.labels else None,
                })
            not_ready = sum(1 for n in node_health if n["ready"] != "True")
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name,
                "nodes": node_health, "total_nodes": len(node_health), "not_ready_count": not_ready, "error": None,
            }
        except Exception as e:
            logger.error("[eks] get_eks_node_health FAILED: %s", e, exc_info=True)
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


class EKSListNamespacesTool(BaseTool):
    """List all namespaces in the EKS cluster with their status."""

    name = "list_eks_namespaces"
    source = "eks"
    description = "List all namespaces in the EKS cluster with their status."
    use_cases = [
        "Discovering what namespaces are present before querying pods/deployments",
        "Confirming an alert namespace actually exists in the cluster",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}

    def run(self, cluster_name: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] list_eks_namespaces cluster=%s", cluster_name)
        try:
            core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
            ns_list = core_v1.list_namespace()
            namespaces = [{"name": ns.metadata.name, "status": ns.status.phase, "labels": ns.metadata.labels or {}} for ns in ns_list.items]
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name,
                "namespaces": namespaces, "error": None,
            }
        except Exception as e:
            logger.error("[eks] list_eks_namespaces FAILED: %s", e, exc_info=True)
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


# Backward-compatible aliases
get_eks_pod_logs = EKSPodLogsTool()
list_eks_pods = EKSListPodsTool()
get_eks_events = EKSEventsTool()
get_eks_deployment_status = EKSDeploymentStatusTool()
list_eks_deployments = EKSListDeploymentsTool()
get_eks_node_health = EKSNodeHealthTool()
list_eks_namespaces = EKSListNamespacesTool()
