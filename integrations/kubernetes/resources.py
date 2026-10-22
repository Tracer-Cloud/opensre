"""The Kubernetes resource types the tools can fetch, and the API call behind each.

Kept free of the Kubernetes SDK: the tool definitions read it while the tool
registry loads, and importing the SDK takes most of a second.
"""

from __future__ import annotations

# Maps resource_type string -> (api_key, method_name, is_cluster_scoped)
RESOURCE_DISPATCH: dict[str, tuple[str, str, bool]] = {
    "pod": ("core", "read_namespaced_pod", False),
    "pods": ("core", "read_namespaced_pod", False),
    "deployment": ("apps", "read_namespaced_deployment", False),
    "deployments": ("apps", "read_namespaced_deployment", False),
    "statefulset": ("apps", "read_namespaced_stateful_set", False),
    "statefulsets": ("apps", "read_namespaced_stateful_set", False),
    "daemonset": ("apps", "read_namespaced_daemon_set", False),
    "daemonsets": ("apps", "read_namespaced_daemon_set", False),
    "service": ("core", "read_namespaced_service", False),
    "services": ("core", "read_namespaced_service", False),
    "configmap": ("core", "read_namespaced_config_map", False),
    "configmaps": ("core", "read_namespaced_config_map", False),
    "ingress": ("networking", "read_namespaced_ingress", False),
    "ingresses": ("networking", "read_namespaced_ingress", False),
    "replicaset": ("apps", "read_namespaced_replica_set", False),
    "replicasets": ("apps", "read_namespaced_replica_set", False),
    "persistentvolumeclaim": ("core", "read_namespaced_persistent_volume_claim", False),
    "persistentvolumeclaims": ("core", "read_namespaced_persistent_volume_claim", False),
    "pvc": ("core", "read_namespaced_persistent_volume_claim", False),
    "node": ("core", "read_node", True),
    "nodes": ("core", "read_node", True),
}

__all__ = ["RESOURCE_DISPATCH"]
