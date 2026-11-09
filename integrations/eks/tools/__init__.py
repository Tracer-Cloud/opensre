"""EKS agent tools — one module per tool, re-exported here.

Registration happens by ``@tool`` decorator at import time. The registry
discovers each submodule directly (``tools.registry_discovery`` walks this
package), so these re-exports exist for callers and tests that import from
``integrations.eks.tools``, not to register anything.
"""

from __future__ import annotations

from integrations.eks.tools.eks_deployment_status_tool import get_eks_deployment_status
from integrations.eks.tools.eks_describe_addon_tool import describe_eks_addon
from integrations.eks.tools.eks_describe_cluster_tool import describe_eks_cluster
from integrations.eks.tools.eks_events_tool import get_eks_events
from integrations.eks.tools.eks_list_clusters_tool import list_eks_clusters
from integrations.eks.tools.eks_list_deployments_tool import list_eks_deployments
from integrations.eks.tools.eks_list_namespaces_tool import list_eks_namespaces
from integrations.eks.tools.eks_list_pods_tool import list_eks_pods
from integrations.eks.tools.eks_node_health_tool import get_eks_node_health
from integrations.eks.tools.eks_nodegroup_health_tool import get_eks_nodegroup_health
from integrations.eks.tools.eks_pod_logs_tool import get_eks_pod_logs

__all__ = [
    "describe_eks_addon",
    "describe_eks_cluster",
    "get_eks_deployment_status",
    "get_eks_events",
    "get_eks_node_health",
    "get_eks_nodegroup_health",
    "get_eks_pod_logs",
    "list_eks_clusters",
    "list_eks_deployments",
    "list_eks_namespaces",
    "list_eks_pods",
]
