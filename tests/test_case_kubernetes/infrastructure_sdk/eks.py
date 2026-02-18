"""EKS cluster lifecycle management using boto3."""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.config import delete_outputs, save_outputs
from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
    get_standard_tags_dict,
)
from tests.shared.infrastructure_sdk.resources import ecr, iam, vpc

STACK_NAME = "tracer-eks-k8s-test"
CLUSTER_NAME = "tracer-eks-test"
NODE_GROUP_NAME = "tracer-eks-test-nodes"
ECR_REPO_NAME = "tracer-eks/etl-job"
REGION = DEFAULT_REGION

CLUSTER_ROLE_NAME = "tracer-eks-cluster-role"
NODE_ROLE_NAME = "tracer-eks-node-role"

EKS_CLUSTER_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "eks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

EC2_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

EKS_CLUSTER_POLICIES = [
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
]

EKS_NODE_POLICIES = [
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
]

EKS_UNSUPPORTED_AZS = {"us-east-1e"}

PIPELINE_DIR = Path(__file__).parent.parent / "pipeline_code"


def _run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------

def _create_role_with_trust(
    name: str, trust_policy: dict, managed_policies: list[str],
) -> dict[str, Any]:
    """Create an IAM role with a trust policy and attach managed policies."""
    iam_client = get_boto3_client("iam", REGION)
    tags = get_standard_tags(STACK_NAME)

    try:
        resp = iam_client.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"Role for {STACK_NAME}",
            Tags=tags,
        )
        role_arn = resp["Role"]["Arn"]
        print(f"Created IAM role {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam_client.get_role(RoleName=name)
            role_arn = resp["Role"]["Arn"]
            print(f"IAM role {name} already exists, reusing")
        else:
            raise

    for policy_arn in managed_policies:
        iam.attach_policy(name, policy_arn, REGION)

    time.sleep(5)
    return {"arn": role_arn, "name": name}


def _create_cluster_role() -> dict[str, Any]:
    return _create_role_with_trust(CLUSTER_ROLE_NAME, EKS_CLUSTER_TRUST_POLICY, EKS_CLUSTER_POLICIES)


def _create_node_role() -> dict[str, Any]:
    return _create_role_with_trust(NODE_ROLE_NAME, EC2_TRUST_POLICY, EKS_NODE_POLICIES)


# ---------------------------------------------------------------------------
# EKS Cluster
# ---------------------------------------------------------------------------

def _cluster_exists() -> str | None:
    """Return cluster status if it exists, None otherwise."""
    eks = get_boto3_client("eks", REGION)
    try:
        resp = eks.describe_cluster(name=CLUSTER_NAME)
        return resp["cluster"]["status"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def _create_cluster(cluster_role_arn: str, subnet_ids: list[str]) -> None:
    status = _cluster_exists()
    if status:
        print(f"EKS cluster {CLUSTER_NAME} already exists (status={status})")
        if status == "ACTIVE":
            return
    else:
        eks = get_boto3_client("eks", REGION)
        print(f"Creating EKS cluster {CLUSTER_NAME}...")
        eks.create_cluster(
            name=CLUSTER_NAME,
            roleArn=cluster_role_arn,
            resourcesVpcConfig={"subnetIds": subnet_ids, "endpointPublicAccess": True},
            tags=get_standard_tags_dict(STACK_NAME),
        )

    _wait_for_cluster("ACTIVE", timeout=900)
    print(f"EKS cluster {CLUSTER_NAME} is ACTIVE")


def _wait_for_cluster(target_status: str, timeout: int = 900) -> None:
    eks = get_boto3_client("eks", REGION)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = eks.describe_cluster(name=CLUSTER_NAME)
            status = resp["cluster"]["status"]
            if status == target_status:
                return
            if status == "FAILED":
                raise RuntimeError("EKS cluster entered FAILED state")
            elapsed = int(time.monotonic() - (deadline - timeout))
            print(f"  Cluster status: {status} ({elapsed}s elapsed)")
        except ClientError as e:
            if target_status == "DELETED" and e.response["Error"]["Code"] == "ResourceNotFoundException":
                return
            raise
        time.sleep(15)
    raise TimeoutError(f"EKS cluster did not reach {target_status} within {timeout}s")


# ---------------------------------------------------------------------------
# Node Group
# ---------------------------------------------------------------------------

def _node_group_exists() -> str | None:
    eks = get_boto3_client("eks", REGION)
    try:
        resp = eks.describe_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODE_GROUP_NAME)
        return resp["nodegroup"]["status"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def _create_node_group(node_role_arn: str, subnet_ids: list[str]) -> None:
    status = _node_group_exists()
    if status:
        print(f"Node group {NODE_GROUP_NAME} already exists (status={status})")
        if status == "ACTIVE":
            return
    else:
        eks = get_boto3_client("eks", REGION)
        print(f"Creating managed node group {NODE_GROUP_NAME}...")
        eks.create_nodegroup(
            clusterName=CLUSTER_NAME,
            nodegroupName=NODE_GROUP_NAME,
            nodeRole=node_role_arn,
            subnets=subnet_ids,
            instanceTypes=["t3.medium"],
            scalingConfig={"minSize": 1, "maxSize": 2, "desiredSize": 1},
            amiType="AL2023_x86_64_STANDARD",
            tags=get_standard_tags_dict(STACK_NAME),
        )

    _wait_for_node_group("ACTIVE", timeout=600)
    print(f"Node group {NODE_GROUP_NAME} is ACTIVE")


def _wait_for_node_group(target_status: str, timeout: int = 600) -> None:
    eks = get_boto3_client("eks", REGION)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = eks.describe_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODE_GROUP_NAME)
            status = resp["nodegroup"]["status"]
            if status == target_status:
                return
            if status in ("CREATE_FAILED", "DELETE_FAILED"):
                raise RuntimeError(f"Node group entered {status}")
            elapsed = int(time.monotonic() - (deadline - timeout))
            print(f"  Node group status: {status} ({elapsed}s elapsed)")
        except ClientError as e:
            if target_status == "DELETED" and e.response["Error"]["Code"] == "ResourceNotFoundException":
                return
            raise
        time.sleep(15)
    raise TimeoutError(f"Node group did not reach {target_status} within {timeout}s")


# ---------------------------------------------------------------------------
# ECR + Image
# ---------------------------------------------------------------------------

def _setup_ecr_and_push_image() -> str:
    """Create ECR repo, build and push the ETL job image. Returns full image URI."""
    repo = ecr.create_repository(ECR_REPO_NAME, STACK_NAME, REGION)
    image_uri = ecr.build_and_push(
        dockerfile_path=PIPELINE_DIR,
        repository_uri=repo["uri"],
        tag="latest",
        platform="linux/amd64",
        region=REGION,
    )
    print(f"Pushed image: {image_uri}")
    return image_uri


# ---------------------------------------------------------------------------
# kubeconfig
# ---------------------------------------------------------------------------

def update_kubeconfig() -> None:
    """Configure kubectl to talk to the EKS cluster."""
    print(f"Updating kubeconfig for {CLUSTER_NAME}...")
    _run(
        ["aws", "eks", "update-kubeconfig", "--name", CLUSTER_NAME, "--region", REGION],
        capture=False,
    )


# ---------------------------------------------------------------------------
# Subnet filtering
# ---------------------------------------------------------------------------

def _filter_eks_subnets(subnet_ids: list[str]) -> list[str]:
    """Remove subnets in AZs that EKS doesn't support."""
    ec2 = get_boto3_client("ec2", REGION)
    resp = ec2.describe_subnets(SubnetIds=subnet_ids)
    filtered = [
        s["SubnetId"]
        for s in resp["Subnets"]
        if s["AvailabilityZone"] not in EKS_UNSUPPORTED_AZS
    ]
    excluded = len(subnet_ids) - len(filtered)
    if excluded:
        print(f"Excluded {excluded} subnet(s) in unsupported AZs: {EKS_UNSUPPORTED_AZS}")
    return filtered


# ---------------------------------------------------------------------------
# EKS access management
# ---------------------------------------------------------------------------

CI_IAM_PRINCIPAL = "arn:aws:iam::395261708130:user/github-actions-ci-readonly"


def _enable_api_auth_mode() -> None:
    """Switch cluster to API_AND_CONFIG_MAP auth so access entries work."""
    eks_client = get_boto3_client("eks", REGION)
    try:
        resp = eks_client.describe_cluster(name=CLUSTER_NAME)
        mode = resp["cluster"]["accessConfig"]["authenticationMode"]
        if mode == "API_AND_CONFIG_MAP":
            return
        print("Enabling API_AND_CONFIG_MAP authentication mode...")
        eks_client.update_cluster_config(
            name=CLUSTER_NAME,
            accessConfig={"authenticationMode": "API_AND_CONFIG_MAP"},
        )
        _wait_for_cluster("ACTIVE", timeout=120)
    except ClientError:
        pass


def _grant_ci_access() -> None:
    """Grant the CI IAM principal cluster admin access."""
    eks_client = get_boto3_client("eks", REGION)
    try:
        eks_client.create_access_entry(
            clusterName=CLUSTER_NAME,
            principalArn=CI_IAM_PRINCIPAL,
            type="STANDARD",
        )
        print(f"Created access entry for {CI_IAM_PRINCIPAL}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise

    with contextlib.suppress(ClientError):
        eks_client.associate_access_policy(
            clusterName=CLUSTER_NAME,
            principalArn=CI_IAM_PRINCIPAL,
            policyArn="arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
            accessScope={"type": "cluster"},
        )


# ---------------------------------------------------------------------------
# Deploy / Destroy orchestration
# ---------------------------------------------------------------------------

def deploy_eks_stack() -> dict[str, Any]:
    """Deploy the full EKS stack: IAM, cluster, nodes, ECR image."""
    print(f"\n{'=' * 60}")
    print(f"Deploying EKS stack: {STACK_NAME}")
    print(f"{'=' * 60}\n")

    cluster_role = _create_cluster_role()
    node_role = _create_node_role()

    vpc_info = vpc.get_default_vpc(REGION)
    subnet_ids = vpc.get_public_subnets(vpc_info["vpc_id"], REGION)
    subnet_ids = _filter_eks_subnets(subnet_ids)
    print(f"Using VPC {vpc_info['vpc_id']} with {len(subnet_ids)} subnets")

    _create_cluster(cluster_role["arn"], subnet_ids)
    _enable_api_auth_mode()
    _grant_ci_access()
    _create_node_group(node_role["arn"], subnet_ids)

    image_uri = _setup_ecr_and_push_image()

    update_kubeconfig()

    outputs = {
        "stack_name": STACK_NAME,
        "cluster_name": CLUSTER_NAME,
        "node_group_name": NODE_GROUP_NAME,
        "cluster_role_arn": cluster_role["arn"],
        "node_role_arn": node_role["arn"],
        "ecr_repo_name": ECR_REPO_NAME,
        "ecr_image_uri": image_uri,
        "vpc_id": vpc_info["vpc_id"],
        "subnet_ids": subnet_ids,
        "region": REGION,
    }
    save_outputs(STACK_NAME, outputs)

    print("\nEKS stack deployed. Outputs saved.")
    return outputs


def destroy_eks_stack() -> None:
    """Tear down the EKS stack in reverse order."""
    print(f"\n{'=' * 60}")
    print(f"Destroying EKS stack: {STACK_NAME}")
    print(f"{'=' * 60}\n")

    eks = get_boto3_client("eks", REGION)

    if _node_group_exists():
        print(f"Deleting node group {NODE_GROUP_NAME}...")
        try:
            eks.delete_nodegroup(clusterName=CLUSTER_NAME, nodegroupName=NODE_GROUP_NAME)
            _wait_for_node_group("DELETED", timeout=600)
            print("Node group deleted")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                print(f"Warning: {e}")

    if _cluster_exists():
        print(f"Deleting EKS cluster {CLUSTER_NAME}...")
        try:
            eks.delete_cluster(name=CLUSTER_NAME)
            _wait_for_cluster("DELETED", timeout=600)
            print("EKS cluster deleted")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                print(f"Warning: {e}")

    for role_name, policies in [
        (CLUSTER_ROLE_NAME, EKS_CLUSTER_POLICIES),
        (NODE_ROLE_NAME, EKS_NODE_POLICIES),
    ]:
        for policy_arn in policies:
            iam.detach_policy(role_name, policy_arn, REGION)
        iam.delete_role(role_name, REGION)
        print(f"Deleted IAM role {role_name}")

    ecr.delete_repository(ECR_REPO_NAME, REGION)
    print(f"Deleted ECR repository {ECR_REPO_NAME}")

    delete_outputs(STACK_NAME)
    print("\nEKS stack destroyed.")


def get_ecr_image_uri() -> str:
    """Load saved outputs and return the ECR image URI."""
    from tests.shared.infrastructure_sdk.config import load_outputs
    outputs = load_outputs(STACK_NAME)
    return outputs["ecr_image_uri"]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "destroy":
        destroy_eks_stack()
    else:
        deploy_eks_stack()
