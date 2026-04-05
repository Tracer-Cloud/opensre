#!/usr/bin/env python3
"""Deploy Bedrock Agent test case infrastructure using AWS SDK.

Creates:
- 1 IAM role with bedrock.amazonaws.com trust
- 1 Bedrock Agent with foundation model
- 1 Action group (RETURN_CONTROL) with OpenSRE tool definitions
- 1 Agent alias for invocation
"""

from __future__ import annotations

import time

from tests.deployment.bedrock.infrastructure_sdk.agent import (
    create_action_group,
    create_agent,
    create_alias,
    get_bedrock_tools,
    prepare_agent,
    tools_to_bedrock_functions,
)
from tests.deployment.bedrock.infrastructure_sdk.iam import create_bedrock_agent_role
from tests.shared.infrastructure_sdk.config import save_outputs

STACK_NAME = "tracer-bedrock"
REGION = "us-east-1"


def deploy() -> dict[str, str]:
    """Deploy a Bedrock Agent with OpenSRE tool action groups.

    Returns:
        Dict of output values (agentId, aliasId, roleName, etc.).
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    # 1. IAM role
    print("Creating IAM role...")
    role = create_bedrock_agent_role(
        name=f"{STACK_NAME}-agent-role",
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"  - Role: {role['name']} ({role['arn']})")

    # 2. Create Bedrock Agent
    print("Creating Bedrock Agent...")
    agent = create_agent(
        agent_name=f"{STACK_NAME}-agent",
        role_arn=role["arn"],
        region=REGION,
    )
    agent_id = agent["agentId"]
    print(f"  - Agent ID: {agent_id}")

    # 3. Build action group from tool registry
    print("Building action group from tool registry...")
    tools = get_bedrock_tools()
    functions = tools_to_bedrock_functions(tools)
    print(f"  - Tools: {[t.name for t in tools]}")

    ag = create_action_group(
        agent_id=agent_id,
        agent_version=agent["agentVersion"],
        functions=functions,
        region=REGION,
    )
    print(f"  - Action group ID: {ag['actionGroupId']}")

    # 4. Prepare agent
    print("Preparing agent (this may take a minute)...")
    prepare_agent(agent_id, region=REGION)
    print("  - Agent status: PREPARED")

    # 5. Create alias
    print("Creating agent alias...")
    alias = create_alias(agent_id, region=REGION)
    alias_id = alias["agentAliasId"]
    print(f"  - Alias ID: {alias_id}")

    outputs: dict[str, str] = {
        "AgentId": agent_id,
        "AgentAliasId": alias_id,
        "RoleName": role["name"],
        "RoleArn": role["arn"],
        "ActionGroupId": ag["actionGroupId"],
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
