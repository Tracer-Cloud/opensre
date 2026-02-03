"""AWS resource creators using boto3 SDK."""

from tests.shared.infrastructure_sdk.resources import (
    api_gateway,
    ecr,
    ecs,
    iam,
    lambda_,
    logs,
    s3,
    secrets,
    vpc,
)

__all__ = [
    "api_gateway",
    "ecr",
    "ecs",
    "iam",
    "lambda_",
    "logs",
    "s3",
    "secrets",
    "vpc",
]
