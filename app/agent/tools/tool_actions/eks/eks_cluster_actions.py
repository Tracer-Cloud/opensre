"""EKS cluster-level investigation actions — boto3 backed."""

import logging

from botocore.exceptions import ClientError

from app.agent.tools.clients.eks.eks_client import EKSClient

logger = logging.getLogger(__name__)


def list_eks_clusters(
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    cluster_names: list[str] | None = None,
) -> dict:
    """List EKS clusters in the AWS account.

    Use when you need to discover what EKS clusters exist or confirm a cluster name.
    Returns cluster names scoped to allowed clusters if configured, otherwise all.
    """
    logger.info("[eks] list_eks_clusters role=%s region=%s", role_arn, region)
    try:
        client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
        clusters = client.list_clusters()
        if cluster_names:
            clusters = [c for c in clusters if c in cluster_names]
        logger.info("[eks] clusters found: %s", clusters)
        return {"source": "eks", "available": True, "clusters": clusters, "error": None}
    except ClientError as e:
        logger.error("[eks] list_eks_clusters ClientError: %s", e, exc_info=True)
        return {"source": "eks", "available": False, "clusters": [], "error": str(e)}
    except Exception as e:
        logger.error("[eks] list_eks_clusters FAILED: %s", e, exc_info=True)
        return {"source": "eks", "available": False, "clusters": [], "error": str(e)}


def describe_eks_cluster(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """Describe an EKS cluster — health, version, status, endpoint, logging config.

    Use when investigating cluster-level issues: version mismatches, endpoint problems,
    control plane logging disabled, or cluster in DEGRADED/FAILED status.
    """
    logger.info("[eks] describe_eks_cluster cluster=%s region=%s", cluster_name, region)
    try:
        client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
        cluster = client.describe_cluster(cluster_name)
        logger.info("[eks] cluster %s status=%s version=%s", cluster_name, cluster.get("status"), cluster.get("version"))
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "status": cluster.get("status"),
            "kubernetes_version": cluster.get("version"),
            "endpoint": cluster.get("endpoint"),
            "cluster_role_arn": cluster.get("roleArn"),
            "logging": cluster.get("logging", {}),
            "resources_vpc_config": cluster.get("resourcesVpcConfig", {}),
            "tags": cluster.get("tags", {}),
            "error": None,
        }
    except ClientError as e:
        logger.error("[eks] describe_eks_cluster ClientError: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "error": str(e),
        }
    except Exception as e:
        logger.error("[eks] describe_eks_cluster FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "error": str(e),
        }


def get_eks_nodegroup_health(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    nodegroup_name: str | None = None,
) -> dict:
    """Get EKS node group health — instance types, scaling config, AMI version, health issues.

    Use when pods are unschedulable, nodes are NotReady, or capacity is the suspected cause.
    If nodegroup_name is None, fetches all node groups for the cluster.
    """
    try:
        client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
        nodegroups = [nodegroup_name] if nodegroup_name else client.list_nodegroups(cluster_name)
        results = []
        for ng in nodegroups:
            ng_data = client.describe_nodegroup(cluster_name, ng)
            results.append({
                "name": ng,
                "status": ng_data.get("status"),
                "instance_types": ng_data.get("instanceTypes", []),
                "scaling_config": ng_data.get("scalingConfig", {}),
                "release_version": ng_data.get("releaseVersion"),
                "health": ng_data.get("health", {}),
                "node_role": ng_data.get("nodeRole"),
                "labels": ng_data.get("labels", {}),
                "taints": ng_data.get("taints", []),
            })
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "nodegroups": results,
            "error": None,
        }
    except ClientError as e:
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "error": str(e),
        }
    except Exception as e:
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "error": str(e),
        }


def describe_eks_addon(
    cluster_name: str,
    addon_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
) -> dict:
    """Describe an EKS addon — coredns, kube-proxy, vpc-cni, aws-ebs-csi-driver, etc.

    Use when pods have DNS resolution failures (coredns), networking issues (vpc-cni),
    or storage attachment failures (ebs-csi). Shows addon version, status, and health issues.
    """
    try:
        client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
        addon = client.describe_addon(cluster_name, addon_name)
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "addon_name": addon_name,
            "status": addon.get("status"),
            "addon_version": addon.get("addonVersion"),
            "health": addon.get("health", {}),
            "marketplace_version": addon.get("marketplaceVersion"),
            "error": None,
        }
    except ClientError as e:
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "addon_name": addon_name,
            "error": str(e),
        }
    except Exception as e:
        return {
            "source": "eks",
            "available": False,
            "cluster_name": cluster_name,
            "addon_name": addon_name,
            "error": str(e),
        }
