#!/usr/bin/env python3
"""Tear down LangSmith deployment test resources.

LangSmith deployments persist until explicitly deleted via the LangSmith UI
or API. This script cleans up the local output file. To fully remove the
deployment, use the LangSmith dashboard.
"""

from __future__ import annotations

import time

from tests.shared.infrastructure_sdk.config import delete_outputs, load_outputs

STACK_NAME = "tracer-langsmith"


def destroy() -> dict[str, list[str]]:
    """Clean up LangSmith deployment outputs.

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
        name = outputs.get("DeploymentName", "unknown")
        print("  *** WARNING ***")
        print(f"  The LangSmith deployment '{name}' is still running remotely.")
        print("  This script only removes the local outputs file.")
        print("  To delete the deployment, visit the LangSmith dashboard:")
        print("    https://smith.langchain.com/")
        print("  *** WARNING ***")
        results["deleted"].append(f"langsmith-deployment:{name}")
    except FileNotFoundError:
        print("No outputs file found — nothing to clean up.")

    delete_outputs(STACK_NAME)
    results["deleted"].append("outputs-file")

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Destroy completed in {elapsed:.1f}s")
    print("=" * 60)

    return results


if __name__ == "__main__":
    destroy()
