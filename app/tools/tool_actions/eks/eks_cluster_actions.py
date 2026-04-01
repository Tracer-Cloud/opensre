"""EKS cluster-level investigation tools — boto3 backed."""

from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from app.tools.clients.eks.eks_client import EKSClient
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


class EKSListClustersTool(BaseTool):
    """List EKS clusters in the AWS account."""

    name = "list_eks_clusters"
    source = "eks"
    description = "List EKS clusters in the AWS account."
    use_cases = [
        "Discovering what EKS clusters exist in the account",
        "Confirming a cluster name before running other EKS actions",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "cluster_names": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return _eks_available(sources)

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_names": eks.get("cluster_names", []),
            **_eks_creds(eks),
        }

    def run(self, role_arn: str, external_id: str = "", region: str = "us-east-1", cluster_names: list | None = None, **_kwargs) -> dict:
        logger.info("[eks] list_eks_clusters role=%s region=%s", role_arn, region)
        try:
            client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
            clusters = client.list_clusters()
            if cluster_names:
                clusters = [c for c in clusters if c in cluster_names]
            return {"source": "eks", "available": True, "clusters": clusters, "error": None}
        except ClientError as e:
            return {"source": "eks", "available": False, "clusters": [], "error": str(e)}
        except Exception as e:
            return {"source": "eks", "available": False, "clusters": [], "error": str(e)}


class EKSDescribeClusterTool(BaseTool):
    """Describe an EKS cluster — health, version, status, endpoint, logging config."""

    name = "describe_eks_cluster"
    source = "eks"
    description = "Describe an EKS cluster — health, version, status, endpoint, logging config."
    use_cases = [
        "Investigating cluster-level issues: version mismatches, endpoint problems",
        "Checking if control plane logging is disabled",
        "Verifying cluster status (ACTIVE, DEGRADED, FAILED)",
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
        logger.info("[eks] describe_eks_cluster cluster=%s region=%s", cluster_name, region)
        try:
            client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
            cluster = client.describe_cluster(cluster_name)
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name,
                "status": cluster.get("status"), "kubernetes_version": cluster.get("version"),
                "endpoint": cluster.get("endpoint"), "cluster_role_arn": cluster.get("roleArn"),
                "logging": cluster.get("logging", {}), "resources_vpc_config": cluster.get("resourcesVpcConfig", {}),
                "tags": cluster.get("tags", {}), "error": None,
            }
        except ClientError as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
        except Exception as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


class EKSNodegroupHealthTool(BaseTool):
    """Get EKS node group health — instance types, scaling config, AMI version, health issues."""

    name = "get_eks_nodegroup_health"
    source = "eks"
    description = "Get EKS node group health — instance types, scaling config, AMI version, health issues."
    use_cases = [
        "Investigating when pods are unschedulable or nodes are NotReady",
        "Checking node capacity and scaling configuration",
        "Finding AMI version issues in EKS node groups",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "nodegroup_name": {"type": "string"},
        },
        "required": ["cluster_name", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}

    def run(self, cluster_name: str, role_arn: str, external_id: str = "", region: str = "us-east-1", nodegroup_name: str | None = None, **_kwargs) -> dict:
        try:
            client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
            nodegroups = [nodegroup_name] if nodegroup_name else client.list_nodegroups(cluster_name)
            results = []
            for ng in nodegroups:
                ng_data = client.describe_nodegroup(cluster_name, ng)
                results.append({
                    "name": ng, "status": ng_data.get("status"),
                    "instance_types": ng_data.get("instanceTypes", []),
                    "scaling_config": ng_data.get("scalingConfig", {}),
                    "release_version": ng_data.get("releaseVersion"),
                    "health": ng_data.get("health", {}), "node_role": ng_data.get("nodeRole"),
                    "labels": ng_data.get("labels", {}), "taints": ng_data.get("taints", []),
                })
            return {"source": "eks", "available": True, "cluster_name": cluster_name, "nodegroups": results, "error": None}
        except ClientError as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
        except Exception as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


class EKSDescribeAddonTool(BaseTool):
    """Describe an EKS addon — coredns, kube-proxy, vpc-cni, aws-ebs-csi-driver, etc."""

    name = "describe_eks_addon"
    source = "eks"
    description = "Describe an EKS addon — coredns, kube-proxy, vpc-cni, aws-ebs-csi-driver, etc."
    use_cases = [
        "Investigating DNS resolution failures (coredns)",
        "Checking networking issues (vpc-cni)",
        "Finding storage attachment failures (ebs-csi)",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "addon_name": {"type": "string", "default": "coredns"},
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
        return {"cluster_name": eks["cluster_name"], "addon_name": "coredns", **_eks_creds(eks)}

    def run(self, cluster_name: str, addon_name: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        try:
            client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
            addon = client.describe_addon(cluster_name, addon_name)
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name,
                "addon_name": addon_name, "status": addon.get("status"),
                "addon_version": addon.get("addonVersion"), "health": addon.get("health", {}),
                "marketplace_version": addon.get("marketplaceVersion"), "error": None,
            }
        except ClientError as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "addon_name": addon_name, "error": str(e)}
        except Exception as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "addon_name": addon_name, "error": str(e)}


# Backward-compatible aliases
list_eks_clusters = EKSListClustersTool()
describe_eks_cluster = EKSDescribeClusterTool()
get_eks_nodegroup_health = EKSNodegroupHealthTool()
describe_eks_addon = EKSDescribeAddonTool()
