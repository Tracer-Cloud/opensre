"""Provider deploy configuration validation helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderValidationResult:
    provider: str
    ok: bool
    errors: tuple[str, ...]
    normalized: dict[str, str]


def _string(config: Mapping[str, object], key: str) -> str:
    return str(config.get(key, "")).strip()


def validate_aws_deploy_config(config: Mapping[str, object]) -> ProviderValidationResult:
    """Validate AWS deploy configuration for dry-run checks."""
    region = _string(config, "region") or "us-east-1"
    role_arn = _string(config, "role_arn")
    external_id = _string(config, "external_id")
    access_key_id = _string(config, "access_key_id")
    secret_access_key = _string(config, "secret_access_key")
    session_token = _string(config, "session_token")

    errors: list[str] = []
    if role_arn:
        if not role_arn.startswith("arn:aws:iam::"):
            errors.append("AWS role ARN must start with 'arn:aws:iam::'.")
    else:
        if not access_key_id or not secret_access_key:
            errors.append("Provide role_arn or both access_key_id and secret_access_key.")

    return ProviderValidationResult(
        provider="aws",
        ok=not errors,
        errors=tuple(errors),
        normalized={
            "region": region,
            "role_arn": role_arn,
            "external_id": external_id,
            "access_key_id": access_key_id,
            "secret_access_key": secret_access_key,
            "session_token": session_token,
        },
    )


def validate_vercel_deploy_config(config: Mapping[str, object]) -> ProviderValidationResult:
    """Validate Vercel deploy configuration for dry-run checks."""
    api_token = _string(config, "api_token")
    team_id = _string(config, "team_id")

    errors: list[str] = []
    if not api_token:
        errors.append("VERCEL_API_TOKEN is required.")

    return ProviderValidationResult(
        provider="vercel",
        ok=not errors,
        errors=tuple(errors),
        normalized={"api_token": api_token, "team_id": team_id},
    )


def validate_railway_deploy_config(config: Mapping[str, object]) -> ProviderValidationResult:
    """Validate Railway deploy configuration for dry-run checks."""
    api_token = _string(config, "api_token")
    project_id = _string(config, "project_id")
    service_id = _string(config, "service_id")
    environment_id = _string(config, "environment_id")

    errors: list[str] = []
    if not api_token:
        errors.append("RAILWAY_API_TOKEN is required.")
    if not project_id:
        errors.append("RAILWAY_PROJECT_ID is required.")
    if not service_id:
        errors.append("RAILWAY_SERVICE_ID is required.")

    return ProviderValidationResult(
        provider="railway",
        ok=not errors,
        errors=tuple(errors),
        normalized={
            "api_token": api_token,
            "project_id": project_id,
            "service_id": service_id,
            "environment_id": environment_id,
        },
    )


def dry_run_provider_validation(
    provider: str,
    *,
    env: Mapping[str, str] | None = None,
) -> ProviderValidationResult:
    """Validate provider configuration using environment variables only."""
    source_env = env if env is not None else os.environ
    resolved = provider.strip().lower()

    if resolved == "aws":
        return validate_aws_deploy_config(
            {
                "region": source_env.get(
                    "AWS_REGION", source_env.get("AWS_DEFAULT_REGION", "us-east-1")
                ),
                "role_arn": source_env.get("AWS_ROLE_ARN", ""),
                "external_id": source_env.get("AWS_EXTERNAL_ID", ""),
                "access_key_id": source_env.get("AWS_ACCESS_KEY_ID", ""),
                "secret_access_key": source_env.get("AWS_SECRET_ACCESS_KEY", ""),
                "session_token": source_env.get("AWS_SESSION_TOKEN", ""),
            }
        )

    if resolved == "vercel":
        return validate_vercel_deploy_config(
            {
                "api_token": source_env.get("VERCEL_API_TOKEN", ""),
                "team_id": source_env.get("VERCEL_TEAM_ID", ""),
            }
        )

    if resolved == "railway":
        return validate_railway_deploy_config(
            {
                "api_token": source_env.get("RAILWAY_API_TOKEN", ""),
                "project_id": source_env.get("RAILWAY_PROJECT_ID", ""),
                "service_id": source_env.get("RAILWAY_SERVICE_ID", ""),
                "environment_id": source_env.get("RAILWAY_ENVIRONMENT_ID", ""),
            }
        )

    raise ValueError(f"Unsupported provider for deploy validation: {provider}")


__all__ = [
    "ProviderValidationResult",
    "dry_run_provider_validation",
    "validate_aws_deploy_config",
    "validate_railway_deploy_config",
    "validate_vercel_deploy_config",
]
