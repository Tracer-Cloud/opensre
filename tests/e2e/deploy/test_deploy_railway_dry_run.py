from __future__ import annotations

from app.deployment.provider_config import (
    dry_run_provider_validation,
    validate_railway_deploy_config,
)


def test_validate_railway_requires_token_project_and_service() -> None:
    result = validate_railway_deploy_config({})
    assert result.ok is False
    assert "RAILWAY_API_TOKEN is required." in result.errors
    assert "RAILWAY_PROJECT_ID is required." in result.errors
    assert "RAILWAY_SERVICE_ID is required." in result.errors


def test_validate_railway_accepts_complete_config() -> None:
    result = validate_railway_deploy_config(
        {
            "api_token": "rw-123",
            "project_id": "project-1",
            "service_id": "service-1",
            "environment_id": "env-1",
        }
    )
    assert result.ok is True
    assert result.errors == ()


def test_railway_dry_run_reads_env() -> None:
    result = dry_run_provider_validation(
        "railway",
        env={
            "RAILWAY_API_TOKEN": "rw-123",
            "RAILWAY_PROJECT_ID": "project-1",
            "RAILWAY_SERVICE_ID": "service-1",
        },
    )
    assert result.ok is True
