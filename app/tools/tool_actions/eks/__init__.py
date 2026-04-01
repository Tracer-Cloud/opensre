"""EKS investigation tools."""

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.eks.eks_cluster_actions import (
    EKSDescribeAddonTool,
    EKSDescribeClusterTool,
    EKSListClustersTool,
    EKSNodegroupHealthTool,
    describe_eks_addon,
    describe_eks_cluster,
    get_eks_nodegroup_health,
    list_eks_clusters,
)
from app.tools.tool_actions.eks.eks_workload_actions import (
    EKSDeploymentStatusTool,
    EKSEventsTool,
    EKSListDeploymentsTool,
    EKSListNamespacesTool,
    EKSListPodsTool,
    EKSNodeHealthTool,
    EKSPodLogsTool,
    get_eks_deployment_status,
    get_eks_events,
    get_eks_node_health,
    get_eks_pod_logs,
    list_eks_deployments,
    list_eks_namespaces,
    list_eks_pods,
)

TOOLS: list[BaseTool] = [
    EKSListClustersTool(),
    EKSDescribeClusterTool(),
    EKSNodegroupHealthTool(),
    EKSDescribeAddonTool(),
    EKSPodLogsTool(),
    EKSEventsTool(),
    EKSDeploymentStatusTool(),
    EKSListDeploymentsTool(),
    EKSNodeHealthTool(),
    EKSListPodsTool(),
    EKSListNamespacesTool(),
]

__all__ = [
    "TOOLS",
    "EKSDescribeAddonTool",
    "EKSDescribeClusterTool",
    "EKSDeploymentStatusTool",
    "EKSEventsTool",
    "EKSListClustersTool",
    "EKSListDeploymentsTool",
    "EKSListNamespacesTool",
    "EKSListPodsTool",
    "EKSNodeHealthTool",
    "EKSNodegroupHealthTool",
    "EKSPodLogsTool",
    # Backward-compatible aliases
    "describe_eks_addon",
    "describe_eks_cluster",
    "get_eks_deployment_status",
    "get_eks_events",
    "get_eks_node_health",
    "get_eks_pod_logs",
    "get_eks_nodegroup_health",
    "list_eks_clusters",
    "list_eks_deployments",
    "list_eks_namespaces",
    "list_eks_pods",
]
