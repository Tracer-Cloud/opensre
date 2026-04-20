"""EC2 instance management for deployment tests.

Launches an EC2 instance with Docker, runs the OpenSRE container, and
exposes the LangGraph API on port 2024.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time

from botocore.exceptions import ClientError

from app.deployment.health import poll_deployment_health
from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)

logger = logging.getLogger(__name__)

INSTANCE_TYPE = "t3.medium"
HEALTH_POLL_INTERVAL = 15
HEALTH_MAX_ATTEMPTS = 60  # 15 min total — ECR pull + container startup


def get_latest_al2023_ami(region: str = DEFAULT_REGION) -> str:
    """Find the latest Amazon Linux 2023 x86_64 AMI.

    Returns:
        AMI ID string.
    """
    ssm = get_boto3_client("ssm", region)
    resp = ssm.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )
    return str(resp["Parameter"]["Value"])


ECR_IMAGE_URI = "395261708130.dkr.ecr.us-east-1.amazonaws.com/opensre:latest"
ECR_REGION = "us-east-1"
ECR_ACCOUNT_ID = "395261708130"


def generate_user_data(env_vars: dict[str, str] | None = None) -> str:
    """Generate a cloud-init user data script that pulls from ECR and runs OpenSRE.

    The script:
    1. Installs Docker and AWS CLI
    2. Authenticates with ECR
    3. Pulls the pre-built image
    4. Runs the container on port 2024
    """
    env_flags = ""
    if env_vars:
        env_flags = " ".join(f"-e {k}='{v}'" for k, v in env_vars.items())

    return f"""\
#!/bin/bash
exec > /var/log/opensre-deploy.log 2>&1
set -euo pipefail

echo "=== Installing Docker ==="
dnf install -y docker aws-cli
systemctl enable docker
systemctl start docker

echo "=== Waiting for IAM role to propagate ==="
sleep 15

echo "=== Authenticating with ECR ==="
for i in 1 2 3 4 5; do
  if aws ecr get-login-password --region {ECR_REGION} | \
     docker login --username AWS --password-stdin {ECR_ACCOUNT_ID}.dkr.ecr.{ECR_REGION}.amazonaws.com; then
    break
  fi
  echo "ECR auth attempt $i failed, retrying in 10s..."
  sleep 10
done

echo "=== Pulling OpenSRE image from ECR ==="
docker pull {ECR_IMAGE_URI}

echo "=== Starting container ==="
docker run -d --name opensre -p 2024:2024 --restart=unless-stopped {env_flags} {ECR_IMAGE_URI}

echo "=== Deployment complete ==="
"""


def create_instance_profile(
    role_name: str,
    profile_name: str,
    stack_name: str,
    region: str = DEFAULT_REGION,
) -> dict[str, str]:
    """Create an IAM instance profile and attach the role.

    Returns:
        Dict with ProfileName and ProfileArn.
    """
    iam = get_boto3_client("iam", region)
    tags = get_standard_tags(stack_name)

    ec2_trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(ec2_trust_policy),
            Description="EC2 instance role for OpenSRE deployment tests",
            Tags=tags,
        )
        role_arn = resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
        else:
            raise

    logger.info("IAM role ready: %s (%s)", role_name, role_arn)

    try:
        iam.create_instance_profile(InstanceProfileName=profile_name, Tags=tags)
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise

    try:
        iam.add_role_to_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "LimitExceeded":
            raise

    # Attach ECR read policy so the instance can pull images
    with contextlib.suppress(ClientError):
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        )

    # Attach Bedrock full-access policy for LLM inference via IAM
    with contextlib.suppress(ClientError):
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AmazonBedrockFullAccess",
        )

    # IAM eventual consistency
    time.sleep(10)

    resp = iam.get_instance_profile(InstanceProfileName=profile_name)
    return {
        "ProfileName": profile_name,
        "ProfileArn": resp["InstanceProfile"]["Arn"],
        "RoleName": role_name,
    }


def delete_instance_profile(
    profile_name: str,
    role_name: str,
    region: str = DEFAULT_REGION,
) -> None:
    """Delete instance profile and associated role."""
    iam = get_boto3_client("iam", region)

    try:
        iam.remove_role_from_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to remove role from profile: %s", e)

    try:
        iam.delete_instance_profile(InstanceProfileName=profile_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to delete instance profile: %s", e)

    try:
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to detach policies: %s", e)

    try:
        inline = iam.list_role_policies(RoleName=role_name)
        for policy_name in inline.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to delete inline policies: %s", e)

    try:
        iam.delete_role(RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise


def launch_instance(
    ami_id: str,
    subnet_id: str,
    security_group_id: str,
    instance_profile_arn: str,
    user_data: str,
    stack_name: str,
    instance_type: str = INSTANCE_TYPE,
    region: str = DEFAULT_REGION,
) -> dict[str, str]:
    """Launch an EC2 instance.

    Returns:
        Dict with InstanceId.
    """
    ec2 = get_boto3_client("ec2", region)
    tags = get_standard_tags(stack_name)
    tags.append({"Key": "Name", "Value": f"{stack_name}-instance"})

    import os as _os

    launch_kwargs: dict = {
        "ImageId": ami_id,
        "InstanceType": instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "SubnetId": subnet_id,
        "SecurityGroupIds": [security_group_id],
        "IamInstanceProfile": {"Arn": instance_profile_arn},
        "UserData": user_data,
        "TagSpecifications": [{"ResourceType": "instance", "Tags": tags}],
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {"VolumeSize": 30, "VolumeType": "gp3"},
            }
        ],
    }
    key_name = _os.getenv("EC2_KEY_NAME")
    if key_name:
        launch_kwargs["KeyName"] = key_name
    resp = ec2.run_instances(**launch_kwargs)

    instance_id = resp["Instances"][0]["InstanceId"]
    logger.info("Launched EC2 instance: %s", instance_id)
    return {"InstanceId": instance_id}


def wait_for_running(instance_id: str, region: str = DEFAULT_REGION) -> dict[str, str]:
    """Wait for an EC2 instance to enter the running state.

    Returns:
        Dict with InstanceId and PublicIpAddress.
    """
    ec2 = get_boto3_client("ec2", region)
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30})

    resp = ec2.describe_instances(InstanceIds=[instance_id])
    instance = resp["Reservations"][0]["Instances"][0]
    public_ip = instance.get("PublicIpAddress", "")

    logger.info("Instance %s running at %s", instance_id, public_ip)
    return {"InstanceId": instance_id, "PublicIpAddress": public_ip}


def wait_for_health(
    public_ip: str,
    port: int = 2024,
    max_attempts: int = HEALTH_MAX_ATTEMPTS,
) -> bool:
    """Wait for the health endpoint to respond on the EC2 instance.

    Raises:
        TimeoutError: If health check doesn't pass.
    """
    base_url = f"http://{public_ip}:{port}"
    status = poll_deployment_health(
        base_url,
        interval_seconds=HEALTH_POLL_INTERVAL,
        max_attempts=max_attempts,
        request_timeout_seconds=5.0,
    )
    logger.info("Health check passed after %d attempts via %s", status.attempts, status.url)
    return True


def terminate_instance(instance_id: str, region: str = DEFAULT_REGION) -> None:
    """Terminate an EC2 instance and wait for termination."""
    ec2 = get_boto3_client("ec2", region)

    try:
        ec2.terminate_instances(InstanceIds=[instance_id])
        waiter = ec2.get_waiter("instance_terminated")
        waiter.wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30})
        logger.info("Instance %s terminated", instance_id)
    except ClientError as e:
        if "InvalidInstanceID.NotFound" not in str(e):
            raise
        logger.warning("Instance %s already terminated", instance_id)
