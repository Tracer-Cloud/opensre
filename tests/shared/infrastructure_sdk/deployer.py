"""Base deployer with common patterns and async support."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import boto3
from botocore.config import Config

DEFAULT_REGION = "us-east-1"

# Thread pool for parallel boto3 operations
_executor = ThreadPoolExecutor(max_workers=10)


def get_boto3_client(service: str, region: str = DEFAULT_REGION) -> Any:
    """Get a boto3 client with standard configuration."""
    config = Config(
        retries={"max_attempts": 3, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=30,
    )
    return boto3.client(service, region_name=region, config=config)  # type: ignore[call-overload]


def get_boto3_resource(service: str, region: str = DEFAULT_REGION) -> Any:
    """Get a boto3 resource with standard configuration."""
    config = Config(
        retries={"max_attempts": 3, "mode": "adaptive"},
        connect_timeout=10,
        read_timeout=30,
    )
    return boto3.resource(service, region_name=region, config=config)  # type: ignore[call-overload]


def get_standard_tags(stack_name: str) -> list[dict[str, str]]:
    """Get standard tags for resources.

    All resources are tagged with:
    - tracer:stack = stack name
    - tracer:managed = sdk
    """
    return [
        {"Key": "tracer:stack", "Value": stack_name},
        {"Key": "tracer:managed", "Value": "sdk"},
    ]


def get_standard_tags_dict(stack_name: str) -> dict[str, str]:
    """Get standard tags as a dict (for services that use dict format)."""
    return {
        "tracer:stack": stack_name,
        "tracer:managed": "sdk",
    }


def get_standard_tags_ecs(stack_name: str) -> list[dict[str, str]]:
    """Get standard tags for ECS resources (uses lowercase key/value)."""
    return [
        {"key": "tracer:stack", "value": stack_name},
        {"key": "tracer:managed", "value": "sdk"},
    ]


async def run_parallel(*coroutines: Any) -> list[Any]:
    """Run multiple async operations in parallel.

    Args:
        *coroutines: Async coroutines to run in parallel.

    Returns:
        List of results from all coroutines.
    """
    return await asyncio.gather(*coroutines)


async def run_in_executor(func: Any, *args: Any) -> Any:
    """Run sync boto3 call in thread pool.

    Args:
        func: Synchronous function to run.
        *args: Arguments to pass to the function.

    Returns:
        Result of the function call.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, func, *args)


def wait_for_condition(
    check_func: Any,
    max_attempts: int = 60,
    delay_seconds: int = 5,
    description: str = "condition",
) -> bool:
    """Wait for a condition to be true.

    Args:
        check_func: Function that returns True when condition is met.
        max_attempts: Maximum number of attempts.
        delay_seconds: Delay between attempts.
        description: Description for error messages.

    Returns:
        True if condition was met.

    Raises:
        TimeoutError: If condition not met within max attempts.
    """
    import time

    for _attempt in range(max_attempts):
        try:
            if check_func():
                return True
        except Exception:
            # AWS resource may not be ready yet; retry until timeout
            pass
        time.sleep(delay_seconds)

    raise TimeoutError(f"Timeout waiting for {description} after {max_attempts * delay_seconds}s")
