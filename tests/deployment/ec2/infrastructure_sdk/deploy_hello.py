#!/usr/bin/env python3
"""Deploy a hello-world container on EC2 in <60 seconds.

Creates:
- 1 IAM role + instance profile (reused if already exists)
- 1 Security group allowing port 8080
- 1 EC2 Nitro instance (ECS-optimized AMI, Docker pre-installed)
"""

from __future__ import annotations

import time
from pathlib import Path

from tests.deployment.ec2.infrastructure_sdk.fast_instance import (
    HELLO_IMAGE_TAG,
    HELLO_PORT,
    INSTANCE_TYPE,
    generate_hello_user_data,
    get_ecs_optimized_ami,
    wait_for_hello,
)
from tests.deployment.ec2.infrastructure_sdk.instance import (
    create_instance_profile,
    launch_instance,
    wait_for_running,
)
from tests.shared.infrastructure_sdk.config import save_outputs
from tests.shared.infrastructure_sdk.deployer import DEFAULT_REGION
from tests.shared.infrastructure_sdk.resources import ecr
from tests.shared.infrastructure_sdk.resources.vpc import (
    create_security_group,
    get_default_vpc,
    get_public_subnets,
)

STACK_NAME = "tracer-ec2-hello"
REGION = DEFAULT_REGION
HELLO_WORLD_DIR = Path(__file__).resolve().parent.parent / "hello_world"
ECR_REPO_NAME = "opensre"


def deploy() -> dict[str, str]:
    """Build the hello-world image, push to ECR, launch EC2, and wait for /ping.

    Returns:
        Dict of output values (InstanceId, PublicIpAddress, etc.).
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME} (target: <60 s)")
    print("=" * 60)
    print()

    # 0. Build and push hello-world image to ECR
    print("Building and pushing hello-world image to ECR...")
    repo = ecr.create_repository(ECR_REPO_NAME, STACK_NAME, REGION)
    image_uri = ecr.build_and_push(
        dockerfile_path=HELLO_WORLD_DIR,
        repository_uri=repo["uri"],
        tag=HELLO_IMAGE_TAG,
        platform="linux/amd64",
        region=REGION,
    )
    print(f"  - Image: {image_uri}")

    # 1. Networking
    print("Getting VPC and subnet...")
    vpc = get_default_vpc(REGION)
    subnets = get_public_subnets(vpc["vpc_id"], REGION)
    subnet_id = subnets[0]
    print(f"  - VPC: {vpc['vpc_id']}")
    print(f"  - Subnet: {subnet_id}")

    # 2. Security group (idempotent — reuses if exists)
    print("Creating security group...")
    sg = create_security_group(
        name=f"{STACK_NAME}-sg",
        vpc_id=vpc["vpc_id"],
        description="Allow hello-world HTTP port",
        ingress_rules=[
            {"port": HELLO_PORT, "cidr": "0.0.0.0/0", "description": "Hello HTTP"},
        ],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Security group: {sg['group_id']}")

    # 3. IAM instance profile (idempotent — reuses if exists)
    print("Creating IAM instance profile...")
    profile = create_instance_profile(
        role_name=f"{STACK_NAME}-role",
        profile_name=f"{STACK_NAME}-profile",
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Profile: {profile['ProfileName']}")

    # 4. ECS-optimized AMI (Docker pre-installed)
    print("Looking up ECS-optimized AMI...")
    ami_id = get_ecs_optimized_ami(REGION)
    print(f"  - AMI: {ami_id}")

    # 5. User data
    user_data = generate_hello_user_data()

    # 6. Launch instance
    print("Launching EC2 instance...")
    instance = launch_instance(
        ami_id=ami_id,
        subnet_id=subnet_id,
        security_group_id=sg["group_id"],
        instance_profile_arn=profile["ProfileArn"],
        user_data=user_data,
        stack_name=STACK_NAME,
        instance_type=INSTANCE_TYPE,
        region=REGION,
    )
    print(f"  - Instance ID: {instance['InstanceId']}")

    # 7. Wait for running
    launch_time = time.time()
    print("Waiting for instance to start...")
    running = wait_for_running(instance["InstanceId"], REGION)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    # 8. Wait for /ping
    print("Waiting for hello-world /ping ...")
    wait_for_hello(public_ip)
    ping_elapsed = time.time() - launch_time
    print(f"  - /ping OK  ({ping_elapsed:.1f}s since launch API call)")

    outputs = {
        "InstanceId": instance["InstanceId"],
        "PublicIpAddress": public_ip,
        "SecurityGroupId": sg["group_id"],
        "ProfileName": profile["ProfileName"],
        "RoleName": profile["RoleName"],
        "AmiId": ami_id,
        "SubnetId": subnet_id,
        "VpcId": vpc["vpc_id"],
    }

    save_outputs(STACK_NAME, outputs)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Deployment completed in {elapsed:.1f}s")
    print(f"  (launch-to-ping: {ping_elapsed:.1f}s)")
    print("=" * 60)
    print()
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


if __name__ == "__main__":
    deploy()
