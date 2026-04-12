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
from tests.shared.infrastructure_sdk.resources import api_gateway, ecr, iam, lambda_, s3, vpc

STACK_NAME = "tracer-eks-k8s-test"
CLUSTER_NAME = "tracer-eks-test"
NODE_GROUP_NAME = "tracer-eks-test-nodes"
ECR_REPO_NAME = "tracer-eks/etl-job"
REGION = DEFAULT_REGION
K8S_VERSION = "1.35"

EKS_ADDONS = ["kube-proxy", "vpc-cni", "coredns"]

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
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",
]

EKS_UNSUPPORTED_AZS = {"us-east-1e"}

PIPELINE_DIR = Path(__file__).parent.parent / "pipeline_code"


def _run(
    cmd: list[str], *, check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------


def _create_role_with_trust(
    name: str,
    trust_policy: dict,
    managed_policies: list[str],
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
    return _create_role_with_trust(
        CLUSTER_ROLE_NAME, EKS_CLUSTER_TRUST_POLICY, EKS_CLUSTER_POLICIES
    )


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
            version=K8S_VERSION,
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
            if (
                target_status == "DELETED"
                and e.response["Error"]["Code"] == "ResourceNotFoundException"
            ):
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
            if (
                target_status == "DELETED"
                and e.response["Error"]["Code"] == "ResourceNotFoundException"
            ):
                return
            raise
        time.sleep(15)
    raise TimeoutError(f"Node group did not reach {target_status} within {timeout}s")


# ---------------------------------------------------------------------------
# EKS Add-ons
# ---------------------------------------------------------------------------


def _get_latest_addon_version(addon_name: str) -> str:
    """Look up the latest compatible version for an EKS add-on."""
    eks = get_boto3_client("eks", REGION)
    resp = eks.describe_addon_versions(
        kubernetesVersion=K8S_VERSION,
        addonName=addon_name,
    )
    return resp["addons"][0]["addonVersions"][0]["addonVersion"]


def _install_addon(addon_name: str) -> None:
    """Install or update a single EKS managed add-on."""
    eks = get_boto3_client("eks", REGION)

    try:
        resp = eks.describe_addon(clusterName=CLUSTER_NAME, addonName=addon_name)
        status = resp["addon"]["status"]
        print(f"Add-on {addon_name} already exists (status={status})")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    version = _get_latest_addon_version(addon_name)
    print(f"Installing add-on {addon_name} ({version})...")
    eks.create_addon(
        clusterName=CLUSTER_NAME,
        addonName=addon_name,
        addonVersion=version,
        resolveConflicts="OVERWRITE",
        tags=get_standard_tags_dict(STACK_NAME),
    )


def _wait_for_addon(addon_name: str, timeout: int = 300) -> None:
    eks = get_boto3_client("eks", REGION)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = eks.describe_addon(clusterName=CLUSTER_NAME, addonName=addon_name)
        status = resp["addon"]["status"]
        if status == "ACTIVE":
            return
        if status in ("CREATE_FAILED", "DEGRADED"):
            raise RuntimeError(f"Add-on {addon_name} entered {status}")
        time.sleep(10)
    raise TimeoutError(f"Add-on {addon_name} did not become ACTIVE within {timeout}s")


def _install_addons() -> None:
    """Install all EKS managed add-ons and wait for them to become active."""
    for addon in EKS_ADDONS:
        _install_addon(addon)
    for addon in EKS_ADDONS:
        _wait_for_addon(addon)
        print(f"Add-on {addon} is ACTIVE")


def _delete_addons() -> None:
    """Delete all EKS managed add-ons (best effort)."""
    eks = get_boto3_client("eks", REGION)
    for addon in EKS_ADDONS:
        try:
            eks.delete_addon(clusterName=CLUSTER_NAME, addonName=addon)
            print(f"Deleted add-on {addon}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                print(f"Warning deleting add-on {addon}: {e}")


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


def cluster_exists() -> bool:
    """Return True if the EKS cluster exists."""
    eks_client = get_boto3_client("eks", REGION)
    try:
        eks_client.describe_cluster(name=CLUSTER_NAME)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def ensure_nodegroup_capacity() -> None:
    """Ensure the EKS cluster has an ACTIVE managed node group without redeploying cluster."""
    status = _cluster_exists()
    if status != "ACTIVE":
        raise RuntimeError(
            f"EKS cluster {CLUSTER_NAME} is not ACTIVE (status={status}). "
            "Create or recover the cluster before running tests."
        )

    node_status = _node_group_exists()
    if node_status == "ACTIVE":
        return

    node_role = _create_node_role()
    vpc_info = vpc.get_default_vpc(REGION)
    subnet_ids = vpc.get_public_subnets(vpc_info["vpc_id"], REGION)
    subnet_ids = _filter_eks_subnets(subnet_ids)
    _create_node_group(node_role["arn"], subnet_ids)


# ---------------------------------------------------------------------------
# Subnet filtering
# ---------------------------------------------------------------------------


def _filter_eks_subnets(subnet_ids: list[str]) -> list[str]:
    """Remove subnets in AZs that EKS doesn't support."""
    ec2 = get_boto3_client("ec2", REGION)
    resp = ec2.describe_subnets(SubnetIds=subnet_ids)
    filtered = [
        s["SubnetId"] for s in resp["Subnets"] if s["AvailabilityZone"] not in EKS_UNSUPPORTED_AZS
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
        # Already in the desired mode; update is a no-op
        pass


def _wait_for_auth_mode(expected: str, timeout: int = 180) -> bool:
    """Wait until cluster accessConfig.authenticationMode reaches expected value."""
    eks_client = get_boto3_client("eks", REGION)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            resp = eks_client.describe_cluster(name=CLUSTER_NAME)
            mode = resp["cluster"]["accessConfig"]["authenticationMode"]
            if mode == expected:
                return True
        except ClientError:
            # Cluster may not be reachable yet during creation; retry
            pass
        time.sleep(5)
    return False


def _grant_ci_access() -> None:
    """Grant the CI IAM principal cluster admin access."""
    eks_client = get_boto3_client("eks", REGION)
    if not _wait_for_auth_mode("API_AND_CONFIG_MAP", timeout=240):
        raise RuntimeError(
            "EKS auth mode did not become API_AND_CONFIG_MAP in time; cannot create access entry yet."
        )
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
    _install_addons()
    _create_node_group(node_role["arn"], subnet_ids)

    image_uri = _setup_ecr_and_push_image()

    import uuid

    suffix = uuid.uuid4().hex[:8]
    landing_bucket = s3.create_bucket(f"tracer-k8s-landing-{suffix}", STACK_NAME, REGION)
    processed_bucket = s3.create_bucket(f"tracer-k8s-processed-{suffix}", STACK_NAME, REGION)
    print(f"S3 buckets: {landing_bucket['name']}, {processed_bucket['name']}")

    update_kubeconfig()

    outputs = {
        "stack_name": STACK_NAME,
        "cluster_name": CLUSTER_NAME,
        "node_group_name": NODE_GROUP_NAME,
        "k8s_version": K8S_VERSION,
        "cluster_role_arn": cluster_role["arn"],
        "node_role_arn": node_role["arn"],
        "ecr_repo_name": ECR_REPO_NAME,
        "ecr_image_uri": image_uri,
        "vpc_id": vpc_info["vpc_id"],
        "subnet_ids": subnet_ids,
        "region": REGION,
        "landing_bucket": landing_bucket["name"],
        "processed_bucket": processed_bucket["name"],
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

    _delete_addons()

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


# ---------------------------------------------------------------------------
# Trigger Lambda constants
# ---------------------------------------------------------------------------

TRIGGER_LAMBDA_NAME = "tracer-eks-etl-trigger"
TRIGGER_LAMBDA_ROLE_NAME = "tracer-eks-trigger-lambda-role"
TRIGGER_API_NAME = "tracer-eks-trigger-api"
TRIGGER_LAMBDA_DIR = Path(__file__).parent.parent / "trigger_lambda"

TRIGGER_LAMBDA_POLICIES = [
    iam.LAMBDA_BASIC_EXECUTION_POLICY,
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",
]

_EKS_DESCRIBE_POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": ["eks:DescribeCluster"],
            "Resource": "*",
        }
    ],
}

_TRIGGER_INLINE_POLICY_NAME = "eks-describe"


def deploy_trigger_lambda(outputs: dict[str, Any]) -> str:
    """Create the trigger Lambda + API Gateway. Returns the invoke URL."""
    print("\nDeploying trigger Lambda...")

    # 1. IAM role for the Lambda
    role = iam.create_lambda_execution_role(TRIGGER_LAMBDA_ROLE_NAME, STACK_NAME, REGION)
    for policy_arn in TRIGGER_LAMBDA_POLICIES[1:]:  # basic policy already attached
        iam.attach_policy(TRIGGER_LAMBDA_ROLE_NAME, policy_arn, REGION)

    # Inline policy: describe EKS cluster
    iam_client = get_boto3_client("iam", REGION)
    iam_client.put_role_policy(
        RoleName=TRIGGER_LAMBDA_ROLE_NAME,
        PolicyName=_TRIGGER_INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(_EKS_DESCRIBE_POLICY_DOCUMENT),
    )
    time.sleep(5)  # IAM propagation

    # 2. Grant Lambda role access to EKS cluster
    _enable_api_auth_mode()
    eks_client = get_boto3_client("eks", REGION)
    for attempt in range(10):
        try:
            eks_client.create_access_entry(
                clusterName=CLUSTER_NAME,
                principalArn=role["arn"],
                type="STANDARD",
            )
            print("  Created EKS access entry for Lambda role")
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ResourceInUseException":
                break
            if (
                code == "InvalidRequestException"
                and "authentication mode" in str(e)
                and attempt < 9
            ):
                time.sleep(10)
                continue
            raise

    with contextlib.suppress(ClientError):
        eks_client.associate_access_policy(
            clusterName=CLUSTER_NAME,
            principalArn=role["arn"],
            policyArn="arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
            accessScope={"type": "cluster"},
        )

    # 3. Fetch cluster info for Lambda env vars
    cluster_info = eks_client.describe_cluster(name=CLUSTER_NAME)["cluster"]
    cluster_endpoint = cluster_info["endpoint"]
    cluster_ca = cluster_info["certificateAuthority"]["data"]

    # 4. Build and deploy Lambda
    code_zip = lambda_.bundle_single_file(TRIGGER_LAMBDA_DIR / "handler.py")
    func = lambda_.create_function(
        name=TRIGGER_LAMBDA_NAME,
        role_arn=role["arn"],
        handler="handler.lambda_handler",
        code_zip=code_zip,
        timeout=120,
        memory=256,
        environment={
            "CLUSTER_NAME": CLUSTER_NAME,
            "CLUSTER_ENDPOINT": cluster_endpoint,
            "CLUSTER_CA_DATA": cluster_ca,
            "LANDING_BUCKET": outputs["landing_bucket"],
            "PROCESSED_BUCKET": outputs["processed_bucket"],
            "IMAGE_URI": outputs["ecr_image_uri"],
            "NAMESPACE": "tracer-test",
            "SERVICE_ACCOUNT": "etl-pipeline-sa",
        },
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  Lambda deployed: {func['arn']}")

    # 5. API Gateway
    api = api_gateway.create_simple_api_with_lambda(
        api_name=TRIGGER_API_NAME,
        lambda_arn=func["arn"],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  API Gateway: {api['invoke_url']}")

    return api["invoke_url"]


def destroy_trigger_lambda() -> None:
    """Tear down trigger Lambda and API Gateway."""
    print("\nDestroying trigger Lambda...")

    # Find and delete API Gateway
    api_client = get_boto3_client("apigateway", REGION)
    try:
        apis = api_client.get_rest_apis()["items"]
        for a in apis:
            if a["name"] == TRIGGER_API_NAME:
                api_gateway.delete_api(a["id"], REGION)
                print(f"  Deleted API {TRIGGER_API_NAME}")
                break
    except Exception as e:
        print(f"  Warning deleting API: {e}")

    lambda_.delete_function(TRIGGER_LAMBDA_NAME, REGION)
    print(f"  Deleted Lambda {TRIGGER_LAMBDA_NAME}")

    iam.detach_policy(TRIGGER_LAMBDA_ROLE_NAME, iam.LAMBDA_BASIC_EXECUTION_POLICY, REGION)
    for policy_arn in TRIGGER_LAMBDA_POLICIES[1:]:
        iam.detach_policy(TRIGGER_LAMBDA_ROLE_NAME, policy_arn, REGION)
    iam.delete_role(TRIGGER_LAMBDA_ROLE_NAME, REGION)
    print(f"  Deleted role {TRIGGER_LAMBDA_ROLE_NAME}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "destroy":
        destroy_eks_stack()
    else:
        deploy_eks_stack()
