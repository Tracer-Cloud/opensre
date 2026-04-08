#!/usr/bin/env python3
"""Destroy Bedrock Agent test case infrastructure."""

from __future__ import annotations

import time

from botocore.exceptions import ClientError

from tests.deployment.bedrock.infrastructure_sdk.agent import delete_agent
from tests.deployment.bedrock.infrastructure_sdk.iam import delete_bedrock_agent_role
from tests.shared.infrastructure_sdk.config import delete_outputs, load_outputs

STACK_NAME = "tracer-bedrock"
REGION = "us-east-1"


def destroy() -> dict[str, list[str]]:
    """Destroy all Bedrock Agent resources.

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

    agent_id = outputs.get("AgentId", "")
    alias_id = outputs.get("AgentAliasId", "")
    role_name = outputs.get("RoleName", f"{STACK_NAME}-agent-role")

    if agent_id:
        print(f"Deleting Bedrock Agent {agent_id}...")
        try:
            delete_agent(agent_id, alias_id, region=REGION)
            results["deleted"].append(f"bedrock-agent:{agent_id}")
            if alias_id:
                results["deleted"].append(f"bedrock-agent-alias:{alias_id}")
            print("  - Agent deleted")
        except ClientError as e:
            msg = f"bedrock-agent:{agent_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    if role_name:
        print(f"Deleting IAM role {role_name}...")
        try:
            delete_bedrock_agent_role(role_name, REGION)
            results["deleted"].append(f"iam-role:{role_name}")
            print("  - Role deleted")
        except ClientError as e:
            msg = f"iam-role:{role_name} - {e}"
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
