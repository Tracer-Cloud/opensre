"""Environment validation helpers for agent clients."""

from __future__ import annotations

import os
from typing import Any


def _missing_env_error(missing: list[str], *, context: str, hint: str | None = None) -> dict[str, Any]:
    error = f"Missing required environment variables for {context}: {', '.join(missing)}"
    payload: dict[str, Any] = {
        "success": False,
        "error": error,
        "missing_env": missing,
        "context": context,
    }
    if hint:
        payload["hint"] = hint
    return payload


def require_env(keys: list[str], *, context: str, hint: str | None = None) -> dict[str, Any] | None:
    missing = [key for key in keys if not os.getenv(key)]
    if not missing:
        return None
    return _missing_env_error(missing, context=context, hint=hint)


def make_boto3_client(service: str):
    """Return a boto3 client for the given service using the configured AWS region."""
    try:
        import boto3 as _boto3
    except ImportError:
        return None
    return _boto3.client(service, region_name=os.getenv("AWS_REGION", "us-east-1"))  # type: ignore[call-overload]


def require_aws_credentials(*, context: str) -> dict[str, Any] | None:
    try:
        import boto3
    except ImportError:
        return {
            "success": False,
            "error": "boto3 not available",
            "context": context,
        }

    session = boto3.session.Session()
    credentials = session.get_credentials()
    if credentials is not None:
        return None

    missing: list[str] = []
    if not os.getenv("AWS_ACCESS_KEY_ID"):
        missing.append("AWS_ACCESS_KEY_ID")
    if not os.getenv("AWS_SECRET_ACCESS_KEY"):
        missing.append("AWS_SECRET_ACCESS_KEY")

    hint = (
        "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN if needed), "
        "or configure an IAM role for this runtime."
    )
    if not missing:
        missing = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    return _missing_env_error(missing, context=context, hint=hint)
