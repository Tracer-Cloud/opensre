from __future__ import annotations

from app.deployment.provider_config import (
    dry_run_provider_validation,
    validate_vercel_deploy_config,
)


def test_validate_vercel_requires_api_token() -> None:
    result = validate_vercel_deploy_config({"team_id": "team-123"})
    assert result.ok is False
    assert result.errors == ("VERCEL_API_TOKEN is required.",)


def test_validate_vercel_accepts_api_token() -> None:
    result = validate_vercel_deploy_config({"api_token": "vercel-token", "team_id": "team-123"})
    assert result.ok is True
    assert result.normalized["team_id"] == "team-123"


def test_vercel_dry_run_reads_env() -> None:
    result = dry_run_provider_validation(
        "vercel",
        env={"VERCEL_API_TOKEN": "vercel-token", "VERCEL_TEAM_ID": "team-xyz"},
    )
    assert result.ok is True
