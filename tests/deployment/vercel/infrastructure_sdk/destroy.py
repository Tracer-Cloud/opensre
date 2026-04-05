#!/usr/bin/env python3
"""Destroy Vercel deployment test resources."""

from __future__ import annotations

import time

from tests.deployment.vercel.infrastructure_sdk.client import delete_deployment, delete_project
from tests.shared.infrastructure_sdk.config import delete_outputs, load_outputs

STACK_NAME = "tracer-vercel"


def destroy() -> dict[str, list[str]]:
    """Delete the Vercel deployment and clean up outputs.

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
        print("No outputs file found — nothing to clean up.")
        outputs = {}

    deployment_id = outputs.get("DeploymentId", "")

    if deployment_id:
        print(f"Deleting Vercel deployment {deployment_id}...")
        try:
            delete_deployment(deployment_id)
            results["deleted"].append(f"vercel-deployment:{deployment_id}")
            print("  - Deployment deleted")
        except Exception as e:
            msg = f"vercel-deployment:{deployment_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    project_name = outputs.get("ProjectName", "")
    if project_name:
        print(f"Deleting Vercel project '{project_name}'...")
        try:
            delete_project(project_name)
            results["deleted"].append(f"vercel-project:{project_name}")
            print("  - Project deleted")
        except Exception as e:
            msg = f"vercel-project:{project_name} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    delete_outputs(STACK_NAME)
    results["deleted"].append("outputs-file")

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
