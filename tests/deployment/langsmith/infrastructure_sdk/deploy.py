#!/usr/bin/env python3
"""Deploy OpenSRE to LangSmith/LangGraph Cloud.

Creates a LangGraph deployment using the ``langgraph deploy`` CLI and waits
for the health endpoint to respond.

Prerequisites:
- LANGSMITH_API_KEY (or LANGGRAPH_HOST_API_KEY / LANGCHAIN_API_KEY)
- ``langgraph`` CLI installed (pip install langgraph-cli)
- Docker running
"""

from __future__ import annotations

import time

from tests.deployment.langsmith.infrastructure_sdk.client import (
    check_prerequisites,
    deploy_langgraph,
    get_api_key,
    wait_for_health,
)
from tests.shared.infrastructure_sdk.config import save_outputs

STACK_NAME = "tracer-langsmith"


def deploy() -> dict[str, str]:
    """Deploy OpenSRE to LangSmith and verify health.

    Returns:
        Dict of output values (DeploymentName, DeploymentUrl).
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    prereqs = check_prerequisites()
    missing = [k for k, v in prereqs.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing prerequisites for LangSmith deployment: {missing}. "
            "Ensure LANGSMITH_API_KEY is set, langgraph CLI is installed, "
            "and Docker is running."
        )

    print("Prerequisites OK")

    # 1. Deploy via langgraph CLI
    print("Deploying with langgraph deploy (this may take several minutes)...")
    deployment = deploy_langgraph()
    print(f"  - Name: {deployment['DeploymentName']}")
    print(f"  - URL: {deployment['DeploymentUrl']}")

    # 2. Wait for health
    print("Waiting for deployment to become healthy...")
    api_key = get_api_key()
    wait_for_health(deployment["DeploymentUrl"], api_key=api_key)
    print("  - Health: OK")

    outputs = {
        "DeploymentName": deployment["DeploymentName"],
        "DeploymentUrl": deployment["DeploymentUrl"],
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
