"""EKS workload investigation actions — Kubernetes Python SDK backed."""

import logging

from app.agent.tools.clients.eks.eks_k8s_client import build_k8s_clients

logger = logging.getLogger(__name__)


def get_eks_pod_logs(
    cluster_name: str,
    namespace: str,
    pod_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    tail_lines: int = 100,
) -> dict:
    """Fetch logs from a specific EKS pod.

    Use when you already know the exact pod name and it exists. If you get a 404,
    use list_eks_pods first to discover what pods are actually running.
    """
    logger.info("[eks] get_eks_pod_logs cluster=%s ns=%s pod=%s", cluster_name, namespace, pod_name)
    try:
        core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
        logs = core_v1.read_namespaced_pod_log(
            name=pod_name, namespace=namespace, tail_lines=tail_lines
        )
        logger.info("[eks] pod logs fetched for %s — %d chars", pod_name, len(logs or ""))
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "pod_name": pod_name,
            "logs": logs,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] get_eks_pod_logs FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "pod_name": pod_name,
            "error": str(e),
        }


def list_eks_pods(
    cluster_name: str,
    namespace: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """List all pods in a namespace with their status, phase, restart counts, and conditions.

    Use this FIRST for any pod-related investigation — it discovers what pods actually exist,
    which are crashing/pending/failed, and their restart counts.
    Also lists pods across all namespaces if namespace is 'all'.
    """
    logger.info("[eks] list_eks_pods cluster=%s ns=%s", cluster_name, namespace)
    try:
        core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)

        if namespace == "all":
            pod_list = core_v1.list_pod_for_all_namespaces()
        else:
            pod_list = core_v1.list_namespaced_pod(namespace=namespace)

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
                    state = {
                        "terminated": True,
                        "exit_code": cs.state.terminated.exit_code,
                        "reason": cs.state.terminated.reason,
                        "message": cs.state.terminated.message,
                    }
                containers.append({
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": state,
                })
            conditions = [
                {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                for c in (pod.status.conditions or [])
            ]
            pods.append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "phase": pod.status.phase,
                "node_name": pod.spec.node_name,
                "containers": containers,
                "conditions": conditions,
                "start_time": str(pod.status.start_time),
            })

        failing = [p for p in pods if p["phase"] not in ("Running", "Succeeded")]
        crashing = [
            p for p in pods
            if any(c["restart_count"] > 3 for c in p["containers"])
        ]
        logger.info("[eks] pods=%d failing=%d crashing=%d", len(pods), len(failing), len(crashing))
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "total_pods": len(pods),
            "pods": pods,
            "failing_pods": failing,
            "high_restart_pods": crashing,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] list_eks_pods FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "namespace": namespace,
            "error": str(e),
        }


def get_eks_events(
    cluster_name: str,
    namespace: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """Get Kubernetes Warning events in a namespace.

    Use to find OOMKilled, FailedScheduling, BackOff, Unhealthy, FailedMount events.
    Use namespace='all' to get events across all namespaces.
    """
    logger.info("[eks] get_eks_events cluster=%s ns=%s", cluster_name, namespace)
    try:
        core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)

        if namespace == "all":
            event_list = core_v1.list_event_for_all_namespaces()
        else:
            event_list = core_v1.list_namespaced_event(namespace=namespace)

        warning_events = [
            {
                "namespace": e.metadata.namespace,
                "reason": e.reason,
                "message": e.message,
                "type": e.type,
                "count": e.count,
                "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}",
                "first_time": str(e.first_timestamp),
                "last_time": str(e.last_timestamp),
            }
            for e in event_list.items
            if e.type == "Warning"
        ]
        logger.info("[eks] events fetched — %d warning events", len(warning_events))
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "warning_events": warning_events,
            "total_warning_count": len(warning_events),
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] get_eks_events FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "namespace": namespace,
            "error": str(e),
        }


def get_eks_deployment_status(
    cluster_name: str,
    namespace: str,
    deployment_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """Get EKS deployment rollout status — desired vs ready vs unavailable replicas.

    Use when you know the exact deployment name. If you get a 404, use list_eks_deployments
    to discover what deployments actually exist in the namespace.
    """
    logger.info("[eks] get_eks_deployment_status cluster=%s ns=%s deployment=%s", cluster_name, namespace, deployment_name)
    try:
        _, apps_v1 = build_k8s_clients(cluster_name, role_arn, external_id, region)
        dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        spec = dep.spec
        status = dep.status
        conditions = [
            {
                "type": c.type,
                "status": c.status,
                "reason": c.reason,
                "message": c.message,
            }
            for c in (status.conditions or [])
        ]
        logger.info("[eks] deployment %s — desired=%s ready=%s", deployment_name, spec.replicas, status.ready_replicas)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "deployment_name": deployment_name,
            "desired_replicas": spec.replicas,
            "ready_replicas": status.ready_replicas,
            "available_replicas": status.available_replicas,
            "unavailable_replicas": status.unavailable_replicas,
            "conditions": conditions,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] get_eks_deployment_status FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "deployment_name": deployment_name,
            "error": str(e),
        }


def list_eks_deployments(
    cluster_name: str,
    namespace: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """List all deployments in a namespace with replica counts and availability status.

    Use to discover what deployments exist and which are degraded/unavailable.
    Use namespace='all' to scan all namespaces.
    """
    logger.info("[eks] list_eks_deployments cluster=%s ns=%s", cluster_name, namespace)
    try:
        _, apps_v1 = build_k8s_clients(cluster_name, role_arn, external_id, region)

        if namespace == "all":
            dep_list = apps_v1.list_deployment_for_all_namespaces()
        else:
            dep_list = apps_v1.list_namespaced_deployment(namespace=namespace)

        deployments = []
        for dep in dep_list.items:
            status = dep.status
            desired = dep.spec.replicas or 0
            ready = status.ready_replicas or 0
            unavailable = status.unavailable_replicas or 0
            deployments.append({
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "desired": desired,
                "ready": ready,
                "available": status.available_replicas or 0,
                "unavailable": unavailable,
                "degraded": unavailable > 0 or ready < desired,
            })

        degraded = [d for d in deployments if d["degraded"]]
        logger.info("[eks] deployments=%d degraded=%d", len(deployments), len(degraded))
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "total_deployments": len(deployments),
            "deployments": deployments,
            "degraded_deployments": degraded,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] list_eks_deployments FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "namespace": namespace,
            "error": str(e),
        }


def get_eks_node_health(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """Get health status of all EKS nodes — conditions, capacity, allocatable, pod counts.

    Use when pods are unschedulable, nodes are NotReady, or to check memory/disk pressure.
    """
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
                "name": node.metadata.name,
                "internal_ip": addresses.get("InternalIP"),
                "ready": conditions.get("Ready"),
                "memory_pressure": conditions.get("MemoryPressure"),
                "disk_pressure": conditions.get("DiskPressure"),
                "pid_pressure": conditions.get("PIDPressure"),
                "capacity_cpu": capacity.get("cpu"),
                "capacity_memory": capacity.get("memory"),
                "allocatable_cpu": allocatable.get("cpu"),
                "allocatable_memory": allocatable.get("memory"),
                "instance_type": node.metadata.labels.get("node.kubernetes.io/instance-type") if node.metadata.labels else None,
            })
        not_ready = sum(1 for n in node_health if n["ready"] != "True")
        logger.info("[eks] node health fetched — %d nodes, %d not ready", len(node_health), not_ready)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "nodes": node_health,
            "total_nodes": len(node_health),
            "not_ready_count": not_ready,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] get_eks_node_health FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "error": str(e),
        }


def list_eks_namespaces(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """List all namespaces in the EKS cluster with their status.

    Use when the alert namespace doesn't exist or you need to discover what namespaces
    are present in the cluster before querying pods/deployments/events.
    """
    logger.info("[eks] list_eks_namespaces cluster=%s", cluster_name)
    try:
        core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
        ns_list = core_v1.list_namespace()
        namespaces = [
            {
                "name": ns.metadata.name,
                "status": ns.status.phase,
                "labels": ns.metadata.labels or {},
            }
            for ns in ns_list.items
        ]
        logger.info("[eks] namespaces found: %s", [n["name"] for n in namespaces])
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespaces": namespaces,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] list_eks_namespaces FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "error": str(e),
        }
