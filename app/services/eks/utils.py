"""Shared utilities for EKS services."""

from __future__ import annotations

from typing import Any


def stored_credentials_to_aws_creds(
    credentials: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalize a stored AWS-integration credential dict into the shape that
    ``sts.assume_role().Credentials`` returns (``AccessKeyId`` /
    ``SecretAccessKey`` / ``SessionToken``).

    Returns ``None`` when either required key is missing or falsy, so callers
    can fall through to the AssumeRole / ambient path. An empty or missing
    ``session_token`` is coerced to ``None`` because botocore rejects
    ``SessionToken=""`` but accepts a missing token for plain IAM user keys.
    """
    if not credentials:
        return None
    access_key = credentials.get("access_key_id")
    secret_key = credentials.get("secret_access_key")
    if not access_key or not secret_key:
        return None
    return {
        "AccessKeyId": access_key,
        "SecretAccessKey": secret_key,
        "SessionToken": credentials.get("session_token") or None,
    }
