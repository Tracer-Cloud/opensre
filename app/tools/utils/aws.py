"""Shared AWS credential and region resolution helpers for tools."""

from __future__ import annotations

from typing import Any


def aws_available(sources: dict[str, dict], integration_name: str = "eks") -> bool:
    """Check if the specified AWS-backed integration is verified."""
    return bool(sources.get(integration_name, {}).get("connection_verified"))


def aws_creds(source_data: dict[str, Any], default_region: str = "us-east-1") -> dict[str, str]:
    """Extract standard AWS credentials (role_arn, external_id, region) from source data."""
    return {
        "role_arn": source_data.get("role_arn", ""),
        "external_id": source_data.get("external_id", ""),
        "region": source_data.get("region") or default_region,
    }
