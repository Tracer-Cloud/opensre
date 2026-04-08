#!/usr/bin/env python3
"""Destroy the hello-world EC2 deployment and clean up resources."""

from __future__ import annotations

import time

from botocore.exceptions import ClientError

from tests.deployment.ec2.infrastructure_sdk.instance import (
    delete_instance_profile,
    terminate_instance,
)
from tests.shared.infrastructure_sdk.config import delete_outputs, load_outputs
from tests.shared.infrastructure_sdk.deployer import DEFAULT_REGION
from tests.shared.infrastructure_sdk.resources.vpc import delete_security_group

STACK_NAME = "tracer-ec2-hello"
REGION = DEFAULT_REGION


def destroy() -> dict[str, list[str]]:
    """Terminate the EC2 instance and clean up all resources.

    Returns:
        Dict with deleted/failed resource lists.
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Destroying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    results: dict[str, list[str]] = {"deleted": [], "failed": []}

    try:
        outputs = load_outputs(STACK_NAME)
    except FileNotFoundError:
        print("No outputs file found — attempting cleanup by known names.")
        outputs = {}

    instance_id = outputs.get("InstanceId", "")
    sg_id = outputs.get("SecurityGroupId", "")
    profile_name = outputs.get("ProfileName", f"{STACK_NAME}-profile")
    role_name = outputs.get("RoleName", f"{STACK_NAME}-role")

    # 1. Terminate instance
    if instance_id:
        print(f"Terminating EC2 instance {instance_id}...")
        try:
            terminate_instance(instance_id, REGION)
            results["deleted"].append(f"ec2-instance:{instance_id}")
            print("  - Instance terminated")
        except ClientError as e:
            msg = f"ec2-instance:{instance_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    # 2. Delete security group (wait for instance ENIs to detach)
    if sg_id:
        print(f"Deleting security group {sg_id}...")
        try:
            time.sleep(10)
            delete_security_group(sg_id, REGION)
            results["deleted"].append(f"security-group:{sg_id}")
            print("  - Security group deleted")
        except ClientError as e:
            msg = f"security-group:{sg_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    # 3. Delete IAM instance profile and role
    print(f"Deleting IAM profile {profile_name} and role {role_name}...")
    try:
        delete_instance_profile(profile_name, role_name, REGION)
        results["deleted"].append(f"instance-profile:{profile_name}")
        results["deleted"].append(f"iam-role:{role_name}")
        print("  - Profile and role deleted")
    except ClientError as e:
        msg = f"iam:{profile_name}/{role_name} - {e}"
        results["failed"].append(msg)
        print(f"  - Failed: {e}")

    delete_outputs(STACK_NAME)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Destroy completed in {elapsed:.1f}s")
    print("=" * 60)

    if results["deleted"]:
        print(f"\nDeleted {len(results['deleted'])} resources:")
        for r in results["deleted"]:
            print(f"  - {r}")

    if results["failed"]:
        print(f"\nFailed to delete {len(results['failed'])} resources:")
        for r in results["failed"]:
            print(f"  - {r}")

    return results


if __name__ == "__main__":
    destroy()
