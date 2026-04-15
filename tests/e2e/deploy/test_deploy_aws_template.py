from __future__ import annotations

from app.deployment.provider_config import dry_run_provider_validation, validate_aws_deploy_config


def test_validate_aws_accepts_role_arn() -> None:
    result = validate_aws_deploy_config(
        {
            "region": "us-east-1",
            "role_arn": "arn:aws:iam::123456789012:role/opensre-deploy",
            "external_id": "ext-1",
        }
    )
    assert result.ok is True
    assert result.errors == ()


def test_validate_aws_requires_role_or_static_credentials() -> None:
    result = validate_aws_deploy_config({"region": "us-east-1"})
    assert result.ok is False
    assert "Provide role_arn or both access_key_id and secret_access_key." in result.errors


def test_validate_aws_defaults_blank_region_to_us_east_1() -> None:
    result = validate_aws_deploy_config(
        {
            "region": "",
            "access_key_id": "AKIA123",
            "secret_access_key": "secret",
        }
    )
    assert result.ok is True
    assert result.normalized["region"] == "us-east-1"


def test_aws_dry_run_uses_environment() -> None:
    result = dry_run_provider_validation(
        "aws",
        env={
            "AWS_DEFAULT_REGION": "us-west-2",
            "AWS_ACCESS_KEY_ID": "AKIA123",
            "AWS_SECRET_ACCESS_KEY": "secret",
        },
    )
    assert result.ok is True
    assert result.normalized["region"] == "us-west-2"


def test_aws_dry_run_respects_explicit_empty_environment(monkeypatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_FROM_PROCESS")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_from_process")

    result = dry_run_provider_validation("aws", env={})

    assert result.ok is False
    assert result.normalized["region"] == "us-east-1"
    assert "Provide role_arn or both access_key_id and secret_access_key." in result.errors
