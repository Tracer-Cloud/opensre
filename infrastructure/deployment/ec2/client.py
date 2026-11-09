"""Shared boto3 client helpers for OpenSRE infrastructure deployments."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from infrastructure.deployment.ec2.config import (
    BOTO3_CONNECT_TIMEOUT_SECONDS,
    BOTO3_READ_TIMEOUT_SECONDS,
    BOTO3_RETRY_MAX_ATTEMPTS,
    DEFAULT_REGION,
    MANAGED_TAG_KEY,
    MANAGED_TAG_VALUE,
    STACK_TAG_KEY,
)


def get_boto3_client(service: str, region: str = DEFAULT_REGION) -> Any:
    """Get a boto3 client with standard retry configuration."""
    config = Config(
        retries={"max_attempts": BOTO3_RETRY_MAX_ATTEMPTS, "mode": "adaptive"},
        connect_timeout=BOTO3_CONNECT_TIMEOUT_SECONDS,
        read_timeout=BOTO3_READ_TIMEOUT_SECONDS,
    )
    return boto3.client(service, region_name=region, config=config)  # type: ignore[call-overload]


def get_standard_tags(stack_name: str) -> list[dict[str, str]]:
    """Return standard resource tags for an OpenSRE deployment stack."""
    return [
        {"Key": STACK_TAG_KEY, "Value": stack_name},
        {"Key": MANAGED_TAG_KEY, "Value": MANAGED_TAG_VALUE},
    ]
