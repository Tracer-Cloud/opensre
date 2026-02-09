#!/usr/bin/env python3
"""Destroy Lambda test case infrastructure."""

import time

from tests.shared.infrastructure_sdk.cleanup import destroy_stack

STACK_NAME = "tracer-lambda"


def destroy():
    """Destroy all resources tagged with the stack name."""
    start_time = time.time()
    print("=" * 60)
    print(f"Destroying {STACK_NAME} infrastructure")
    print("=" * 60)
    print()

    result = destroy_stack(STACK_NAME)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Destroy completed in {elapsed:.1f}s")
    print("=" * 60)
    print()

    if result["deleted"]:
        print(f"Deleted {len(result['deleted'])} resources:")
        for arn in result["deleted"]:
            print(f"  - {arn}")

    if result["not_found"]:
        print(f"\nAlready deleted {len(result['not_found'])} resources:")
        for arn in result["not_found"]:
            print(f"  - {arn}")

    if result["failed"]:
        print(f"\nFailed to delete {len(result['failed'])} resources:")
        for item in result["failed"]:
            print(f"  - {item['arn']}: {item['error']}")

    return result


if __name__ == "__main__":
    destroy()
