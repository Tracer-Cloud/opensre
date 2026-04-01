"""EKS cluster-level investigation tools — boto3 backed."""

from __future__ import annotations

from botocore.exceptions import ClientError

from app.tools.clients.eks.eks_client import EKSClient
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.EKSListClustersTool import _eks_available, _eks_creds


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


describe_eks_addon = EKSDescribeAddonTool()
