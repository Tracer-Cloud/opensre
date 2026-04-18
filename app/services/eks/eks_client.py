"""EKS boto3 client with IAM role assumption."""

from typing import Any

import boto3


class EKSClient:
    def __init__(self, role_arn: str, external_id: str = "", region: str = "us-east-1"):
        self._region = region
        self._boto_client = self._build(role_arn, external_id)

    def _build(self, role_arn: str, external_id: str) -> Any:
        sts = boto3.client("sts")
        kwargs: dict = {"RoleArn": role_arn, "RoleSessionName": "TracerEKSInvestigation"}
        if external_id:
            kwargs["ExternalId"] = external_id
        c = sts.assume_role(**kwargs)["Credentials"]
        return boto3.client(
            "eks",
            region_name=self._region,
            aws_access_key_id=c["AccessKeyId"],
            aws_secret_access_key=c["SecretAccessKey"],
            aws_session_token=c["SessionToken"],
        )

    def list_clusters(self) -> list[str]:
        result: list[str] = self._boto_client.list_clusters()["clusters"]
        return result

    def describe_cluster(self, name: str) -> dict[str, Any]:
        result: dict[str, Any] = self._boto_client.describe_cluster(name=name)["cluster"]
        return result

    def list_nodegroups(self, cluster: str) -> list[str]:
        result: list[str] = self._boto_client.list_nodegroups(clusterName=cluster)["nodegroups"]
        return result

    def describe_nodegroup(self, cluster: str, nodegroup: str) -> dict[str, Any]:
        result: dict[str, Any] = self._boto_client.describe_nodegroup(
            clusterName=cluster, nodegroupName=nodegroup
        )["nodegroup"]
        return result

    def list_addons(self, cluster: str) -> list[str]:
        result: list[str] = self._boto_client.list_addons(clusterName=cluster)["addons"]
        return result

    def describe_addon(self, cluster: str, addon: str) -> dict[str, Any]:
        result: dict[str, Any] = self._boto_client.describe_addon(
            clusterName=cluster, addonName=addon
        )["addon"]
        return result
