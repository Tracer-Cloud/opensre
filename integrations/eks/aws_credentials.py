"""Normalize stored AWS credentials for botocore.

Maps internal snake_case credential keys to the PascalCase dict botocore
clients expect.
"""

from __future__ import annotations

from typing import Any


def stored_credentials_to_aws_creds(
    credentials: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Normalize a stored AWS credential dict for use with botocore.

    Takes a dict containing ``access_key_id``, ``secret_access_key``, and
    optionally ``session_token`` (or ``None``). Returns a new dict with
    guaranteed truthy ``AccessKeyId`` and ``SecretAccessKey`` keys, plus
    ``SessionToken`` (which preserves truthy tokens but converts empty or
    missing values to ``None`` because botocore rejects empty strings).
    Returns ``None`` if the input is falsy or missing required keys,
    allowing callers to fall back to ambient credentials. The original
    dictionary is not mutated.
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
