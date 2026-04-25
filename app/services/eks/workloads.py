"""Shared workload listing and formatting helpers for EKS (Kubernetes) resources."""

from __future__ import annotations

from typing import Any

from kubernetes.client import V1Deployment, V1Node, V1Pod


def format_eks_pod(pod: V1Pod) -> dict[str, Any]:
    """Format a Kubernetes V1Pod into a standard tracer dictionary."""
    containers = []
    for cs in pod.status.container_statuses or []:
        state = {}
        if cs.state.running:
            state = {"running": True, "started_at": str(cs.state.running.started_at)}
        elif cs.state.waiting:
            state = {
                "waiting": True,
                "reason": cs.state.waiting.reason,
                "message": cs.state.waiting.message,
            }
        elif cs.state.terminated:
            state = {
                "terminated": True,
                "exit_code": cs.state.terminated.exit_code,
                "reason": cs.state.terminated.reason,
                "message": cs.state.terminated.message,
            }
        containers.append(
            {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count,
                "state": state,
            }
        )

    conditions = [
        {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
        for c in (pod.status.conditions or [])
    ]

    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase,
        "node_name": pod.spec.node_name,
        "containers": containers,
        "conditions": conditions,
        "start_time": str(pod.status.start_time),
    }


def format_eks_deployment(dep: V1Deployment) -> dict[str, Any]:
    """Format a Kubernetes V1Deployment into a standard tracer dictionary."""
    status = dep.status
    desired = dep.spec.replicas or 0
    ready = status.ready_replicas or 0
    unavailable = status.unavailable_replicas or 0

    return {
        "name": dep.metadata.name,
        "namespace": dep.metadata.namespace,
        "desired": desired,
        "ready": ready,
        "available": status.available_replicas or 0,
        "unavailable": unavailable,
        "degraded": unavailable > 0 or ready < desired,
    }


def format_eks_node(node: V1Node) -> dict[str, Any]:
    """Format a Kubernetes V1Node into a standard tracer dictionary."""
    conditions = {c.type: c.status for c in (node.status.conditions or [])}
    capacity = node.status.capacity or {}
    allocatable = node.status.allocatable or {}
    addresses = {a.type: a.address for a in (node.status.addresses or [])}

    return {
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
        "instance_type": node.metadata.labels.get("node.kubernetes.io/instance-type")
        if node.metadata.labels
        else None,
    }
