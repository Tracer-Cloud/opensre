"""Secrets Manager access."""

import json
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import DEFAULT_REGION, get_boto3_client


def get_secret_value(secret_name: str, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Get secret value as dict (JSON parsed).

    Args:
        secret_name: Secret name or ARN.
        region: AWS region.

    Returns:
        Secret value as parsed JSON dict.

    Raises:
        ValueError: If secret doesn't exist or isn't valid JSON.
    """
    secrets_client = get_boto3_client("secretsmanager", region)

    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise ValueError(f"Secret '{secret_name}' not found") from e
        raise

    secret_string = response.get("SecretString")
    if not secret_string:
        raise ValueError(f"Secret '{secret_name}' has no string value (might be binary)")

    try:
        result: dict[str, Any] = json.loads(secret_string)
        return result
    except json.JSONDecodeError as e:
        raise ValueError(f"Secret '{secret_name}' is not valid JSON: {e}") from e


def get_secret_string(secret_name: str, region: str = DEFAULT_REGION) -> str:
    """Get secret value as raw string.

    Args:
        secret_name: Secret name or ARN.
        region: AWS region.

    Returns:
        Secret value as string.

    Raises:
        ValueError: If secret doesn't exist.
    """
    secrets_client = get_boto3_client("secretsmanager", region)

    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise ValueError(f"Secret '{secret_name}' not found") from e
        raise

    secret_string = response.get("SecretString")
    if not secret_string:
        raise ValueError(f"Secret '{secret_name}' has no string value")

    return str(secret_string)


def get_secret_arn(secret_name: str, region: str = DEFAULT_REGION) -> str:
    """Get secret ARN.

    Args:
        secret_name: Secret name.
        region: AWS region.

    Returns:
        Secret ARN.

    Raises:
        ValueError: If secret doesn't exist.
    """
    secrets_client = get_boto3_client("secretsmanager", region)

    try:
        response = secrets_client.describe_secret(SecretId=secret_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise ValueError(f"Secret '{secret_name}' not found") from e
        raise

    return str(response["ARN"])


def secret_exists(secret_name: str, region: str = DEFAULT_REGION) -> bool:
    """Check if a secret exists.

    Args:
        secret_name: Secret name or ARN.
        region: AWS region.

    Returns:
        True if secret exists.
    """
    secrets_client = get_boto3_client("secretsmanager", region)

    try:
        secrets_client.describe_secret(SecretId=secret_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def list_secrets(
    prefix: str | None = None,
    max_results: int = 100,
    region: str = DEFAULT_REGION,
) -> list[dict[str, Any]]:
    """List secrets.

    Args:
        prefix: Optional name prefix filter.
        max_results: Maximum secrets to return.
        region: AWS region.

    Returns:
        List of secret metadata.
    """
    secrets_client = get_boto3_client("secretsmanager", region)

    params: dict[str, Any] = {"MaxResults": min(max_results, 100)}

    if prefix:
        params["Filters"] = [{"Key": "name", "Values": [prefix]}]

    secrets = []
    paginator = secrets_client.get_paginator("list_secrets")

    for page in paginator.paginate(**params):
        for secret in page.get("SecretList", []):
            secrets.append(
                {
                    "name": secret["Name"],
                    "arn": secret["ARN"],
                    "description": secret.get("Description"),
                    "last_changed": secret.get("LastChangedDate"),
                }
            )
            if len(secrets) >= max_results:
                return secrets

    return secrets


def get_secret_for_ecs(secret_name: str, json_key: str | None = None, region: str = DEFAULT_REGION) -> dict[str, str]:
    """Get secret reference for ECS container definition.

    Args:
        secret_name: Secret name.
        json_key: Optional JSON key within the secret.
        region: AWS region.

    Returns:
        Dict with 'name' and 'valueFrom' for ECS secrets config.
    """
    arn = get_secret_arn(secret_name, region)

    value_from = arn
    if json_key:
        value_from = f"{arn}:{json_key}::"

    return {
        "name": json_key or secret_name.split("/")[-1].upper(),
        "valueFrom": value_from,
    }
