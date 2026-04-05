#!/usr/bin/env python3
"""Deploy OpenSRE on an EC2 instance with Docker.

Creates:
- 1 IAM role + instance profile for EC2
- 1 Security group allowing port 2024 (LangGraph API)
- 1 EC2 instance running the OpenSRE Docker container
"""

from __future__ import annotations

import time

from tests.deployment.ec2.infrastructure_sdk.instance import (
    create_instance_profile,
    generate_user_data,
    get_latest_al2023_ami,
    launch_instance,
    wait_for_health,
    wait_for_running,
)
from tests.shared.infrastructure_sdk.config import save_outputs
from tests.shared.infrastructure_sdk.deployer import DEFAULT_REGION
from tests.shared.infrastructure_sdk.resources.vpc import (
    create_security_group,
    get_default_vpc,
    get_public_subnets,
)

STACK_NAME = "tracer-ec2"
REGION = DEFAULT_REGION


def deploy() -> dict[str, str]:
    """Deploy OpenSRE on EC2 with Docker.

    Returns:
        Dict of output values (InstanceId, PublicIpAddress, SecurityGroupId, etc.).
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    # 1. Networking
    print("Getting VPC and subnet...")
    vpc = get_default_vpc(REGION)
    subnets = get_public_subnets(vpc["vpc_id"], REGION)
    subnet_id = subnets[0]
    print(f"  - VPC: {vpc['vpc_id']}")
    print(f"  - Subnet: {subnet_id}")

    # 2. Security group
    print("Creating security group...")
    sg = create_security_group(
        name=f"{STACK_NAME}-sg",
        vpc_id=vpc["vpc_id"],
        description="Allow LangGraph API port for OpenSRE deployment tests",
        ingress_rules=[
            {"port": 2024, "cidr": "0.0.0.0/0", "description": "LangGraph API"},
        ],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Security group: {sg['group_id']}")

    # 3. IAM instance profile
    print("Creating IAM instance profile...")
    profile = create_instance_profile(
        role_name=f"{STACK_NAME}-role",
        profile_name=f"{STACK_NAME}-profile",
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Profile: {profile['ProfileName']}")

    # 4. AMI
    print("Looking up latest Amazon Linux 2023 AMI...")
    ami_id = get_latest_al2023_ami(REGION)
    print(f"  - AMI: {ami_id}")

    # 5. User data
    user_data = generate_user_data()

    # 6. Launch instance
    print("Launching EC2 instance...")
    instance = launch_instance(
        ami_id=ami_id,
        subnet_id=subnet_id,
        security_group_id=sg["group_id"],
        instance_profile_arn=profile["ProfileArn"],
        user_data=user_data,
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Instance ID: {instance['InstanceId']}")

    # 7. Wait for running
    print("Waiting for instance to start...")
    running = wait_for_running(instance["InstanceId"], REGION)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    # 8. Wait for health (Docker build + container start takes time)
    print("Waiting for OpenSRE container health (may take 5-10 minutes)...")
    wait_for_health(public_ip)
    print("  - Health: OK")

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
    print("=" * 60)
    print()
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


if __name__ == "__main__":
    deploy()
