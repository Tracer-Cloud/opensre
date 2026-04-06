#!/usr/bin/env python3
"""Deploy the full OpenSRE investigation server on EC2.

Creates:
- 1 IAM role + instance profile
- 1 Security group allowing ports 22 (SSH) and 8080 (HTTP)
- 1 EC2 t3.medium running the OpenSRE FastAPI server (no Docker)
"""

from __future__ import annotations

import os
import time

from tests.deployment.ec2.infrastructure_sdk.instance import (
    create_instance_profile,
    launch_instance,
    wait_for_running,
)
from tests.deployment.ec2.infrastructure_sdk.remote_instance import (
    INSTANCE_TYPE,
    SERVER_PORT,
    generate_remote_user_data,
    get_latest_al2023_ami,
    wait_for_remote_health,
)
from tests.shared.infrastructure_sdk.config import save_outputs
from tests.shared.infrastructure_sdk.deployer import DEFAULT_REGION
from tests.shared.infrastructure_sdk.resources.vpc import (
    create_security_group,
    get_default_vpc,
    get_public_subnets,
)

STACK_NAME = "tracer-ec2-remote"
REGION = DEFAULT_REGION

_DOTENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")


def deploy(branch: str = "main") -> dict[str, str]:
    """Bootstrap the OpenSRE investigation server on a fresh EC2 instance.

    Returns:
        Dict of output values (InstanceId, PublicIpAddress, etc.).
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME}")
    print("=" * 60)
    print()

    # 1. Networking
    print("Getting VPC and subnet...")
    vpc = get_default_vpc(REGION)
    subnets = get_public_subnets(vpc["vpc_id"], REGION)
    subnet_id = subnets[0]
    print(f"  - VPC: {vpc['vpc_id']}")
    print(f"  - Subnet: {subnet_id}")

    # 2. Security group (SSH + HTTP)
    print("Creating security group...")
    sg = create_security_group(
        name=f"{STACK_NAME}-sg",
        vpc_id=vpc["vpc_id"],
        description="OpenSRE remote server - SSH and investigation API",
        ingress_rules=[
            {"port": 22, "cidr": "0.0.0.0/0", "description": "SSH"},
            {"port": SERVER_PORT, "cidr": "0.0.0.0/0", "description": "Investigation API"},
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

    # 5. Collect env vars from .env file and force Bedrock provider
    env_vars: dict[str, str] = {}
    dotenv_path = os.path.normpath(_DOTENV_PATH)
    if os.path.isfile(dotenv_path):
        with open(dotenv_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key:
                    env_vars[key] = value
    env_vars["LLM_PROVIDER"] = "bedrock"
    env_vars["AWS_REGION"] = REGION
    env_vars.pop("OLLAMA_MODEL", None)
    env_vars.pop("OLLAMA_HOST", None)
    print("  - LLM_PROVIDER: bedrock (Anthropic via Bedrock, IAM auth)")
    print(f"  - Env vars forwarded: {len(env_vars)} keys")

    user_data = generate_remote_user_data(env_vars=env_vars, branch=branch)

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
    print("Waiting for instance to start...")
    running = wait_for_running(instance["InstanceId"], REGION)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    # 8. Wait for server health (git clone + pip install takes time)
    print("Waiting for investigation server (git clone + pip install)...")
    print("  (this can take 3-5 minutes on first deploy)")
    wait_for_remote_health(public_ip)
    print("  - /ok: healthy")

    outputs = {
        "InstanceId": instance["InstanceId"],
        "PublicIpAddress": public_ip,
        "SecurityGroupId": sg["group_id"],
        "ProfileName": profile["ProfileName"],
        "RoleName": profile["RoleName"],
        "AmiId": ami_id,
        "SubnetId": subnet_id,
        "VpcId": vpc["vpc_id"],
        "ServerPort": str(SERVER_PORT),
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
    print()
    key_name = os.getenv("EC2_KEY_NAME")
    if key_name:
        print(f"  SSH:    ssh -i ~/.ssh/{key_name}.pem ec2-user@{public_ip}")
    else:
        print("  SSH:    set EC2_KEY_NAME env var to enable SSH access")
    print(f"  Health: curl http://{public_ip}:{SERVER_PORT}/ok")
    print(f"  Logs:   ssh ec2-user@{public_ip} 'cat /var/log/opensre-remote.log'")

    return outputs


if __name__ == "__main__":
    deploy()
