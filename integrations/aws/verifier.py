"""AWS integration verifier — STS caller-identity probe."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

from integrations.config_models import AWSIntegrationConfig
from integrations.verification import register_verifier, result

ROLE_NEEDS_BASE_CREDENTIALS_MESSAGE = (
    "IAM Role ARN is assumed with your ambient AWS credentials, and none were "
    "found. Configure a base identity that may call sts:AssumeRole on the role "
    "(AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, an `aws configure` profile, or "
    "an attached instance/task role) and run setup again — or choose "
    '"Access Key + Secret" instead.'
)


def _base_sts_client(region: str) -> Any:
    """STS client for OpenSRE's own base identity — the one that assumes the role.

    Built from :func:`integrations.aws.env.base_session`, so a lone
    ``AWS_ACCESS_KEY_ID`` loaded from ``.env`` (its secret lives in the keyring)
    cannot block the profile / instance-role chain. Kept as one seam so tests
    substitute a fake here and never reach STS.
    """
    from integrations.aws.env import base_session

    return base_session(region=region).client("sts")


def _build_sts_client(aws_config: AWSIntegrationConfig) -> tuple[Any, str, str]:
    region = aws_config.region
    role_arn = aws_config.role_arn
    external_id = aws_config.external_id
    if role_arn:
        base_sts_client = _base_sts_client(region)
        assume_role_args: dict[str, str] = {
            "RoleArn": role_arn,
            "RoleSessionName": "TracerIntegrationVerify",
        }
        if external_id:
            assume_role_args["ExternalId"] = external_id
        credentials = base_sts_client.assume_role(**assume_role_args)["Credentials"]
        return (
            boto3.client(
                "sts",
                region_name=region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            ),
            region,
            "assume-role",
        )

    static_credentials = aws_config.credentials
    if static_credentials is None:
        raise ValueError("Missing AWS role_arn or credentials.")
    return (
        boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=static_credentials.access_key_id,
            aws_secret_access_key=static_credentials.secret_access_key,
            aws_session_token=static_credentials.session_token or None,
        ),
        region,
        "static-creds",
    )


@register_verifier("aws")
def verify_aws(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        aws_config = AWSIntegrationConfig.model_validate(config)
    except Exception as err:
        return result("aws", source, "missing", str(err))
    try:
        sts_client, region, mode = _build_sts_client(aws_config)
        identity = sts_client.get_caller_identity()
    except NoCredentialsError:
        # Only the role path reaches STS without explicit keys; static keys
        # would have been passed to the client and this error cannot arise.
        return result("aws", source, "failed", ROLE_NEEDS_BASE_CREDENTIALS_MESSAGE)
    except PartialCredentialsError as exc:
        missing = exc.kwargs.get("cred_var", "one of the AWS_* variables")
        return result(
            "aws",
            source,
            "failed",
            f"This shell has half a key pair in the environment: {missing} is not set. "
            "The role is assumed with those base credentials, so set both "
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (or unset both and use an "
            "`aws configure` profile), then run setup again.",
        )
    except Exception as exc:
        return result("aws", source, "failed", f"AWS STS check failed: {exc}")

    account = str(identity.get("Account", "")).strip()
    arn = str(identity.get("Arn", "")).strip()
    return result(
        "aws",
        source,
        "passed",
        (
            f"Connected to AWS STS via {mode} in {region}; "
            f"caller identity account={account or 'unknown'} arn={arn or 'unknown'}."
        ),
    )
