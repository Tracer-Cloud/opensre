#!/usr/bin/env python3
"""Deploy a health-check serverless function to Vercel.

Creates a minimal Python serverless function deployment on Vercel to validate
the deployment pipeline works end-to-end.

Prerequisites:
- VERCEL_API_TOKEN
- Optional: VERCEL_TEAM_ID (for team-scoped deployments)
"""

from __future__ import annotations

import time

from tests.deployment.vercel.infrastructure_sdk.client import (
    check_health,
    check_prerequisites,
    create_deployment,
    wait_for_deployment,
)
from tests.shared.infrastructure_sdk.config import save_outputs

STACK_NAME = "tracer-vercel"


def deploy() -> dict[str, str]:
    """Deploy a health-check function to Vercel and verify it.

    Returns:
        Dict of output values (DeploymentId, DeploymentUrl, ProjectName).
    """
    start_time = time.time()
    print("=" * 60)
    print(f"Deploying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    prereqs = check_prerequisites()
    if not prereqs["api_token"]:
        raise RuntimeError(
            "VERCEL_API_TOKEN not set. Get a token from https://vercel.com/account/tokens"
        )

    print("Prerequisites OK")

    # 1. Create deployment
    print("Creating Vercel deployment...")
    deployment = create_deployment()
    print(f"  - Deployment ID: {deployment['DeploymentId']}")
    print(f"  - URL: {deployment['DeploymentUrl']}")

    # 2. Wait for READY
    print("Waiting for deployment to become ready...")
    state = wait_for_deployment(deployment["DeploymentId"])
    print(f"  - State: {state}")

    # 3. Verify health
    print("Checking health endpoint...")
    health = check_health(deployment["DeploymentUrl"])
    print(f"  - Health status: {health['status_code']}")

    outputs = {
        "DeploymentId": deployment["DeploymentId"],
        "DeploymentUrl": deployment["DeploymentUrl"],
        "ProjectName": deployment["ProjectName"],
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
