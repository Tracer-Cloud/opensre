"""AWS environment variable names."""

from __future__ import annotations

AWS_PROFILE_ENV = "AWS_PROFILE"
AWS_CONTAINER_CREDENTIALS_RELATIVE_URI_ENV = "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"
AWS_WEB_IDENTITY_TOKEN_FILE_ENV = "AWS_WEB_IDENTITY_TOKEN_FILE"

AWS_ROLE_ARN_ENV = "AWS_ROLE_ARN"
AWS_EXTERNAL_ID_ENV = "AWS_EXTERNAL_ID"
AWS_REGION_ENV = "AWS_REGION"
AWS_ACCESS_KEY_ID_ENV = "AWS_ACCESS_KEY_ID"
AWS_SECRET_ACCESS_KEY_ENV = "AWS_SECRET_ACCESS_KEY"
AWS_SESSION_TOKEN_ENV = "AWS_SESSION_TOKEN"

#: boto3 reads these two as one unit: an access key id in the environment
#: without its secret does not fall through to the next credential source, it
#: raises ``PartialCredentialsError``. The secret is keyring-only, so a plain
#: ``.env`` can legitimately hold the id alone — it must then stay out of the
#: process environment.
AWS_STATIC_KEY_PAIR_ENV: tuple[str, str] = (AWS_ACCESS_KEY_ID_ENV, AWS_SECRET_ACCESS_KEY_ENV)

__all__ = [
    "AWS_ACCESS_KEY_ID_ENV",
    "AWS_PROFILE_ENV",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI_ENV",
    "AWS_WEB_IDENTITY_TOKEN_FILE_ENV",
    "AWS_EXTERNAL_ID_ENV",
    "AWS_REGION_ENV",
    "AWS_ROLE_ARN_ENV",
    "AWS_SECRET_ACCESS_KEY_ENV",
    "AWS_SESSION_TOKEN_ENV",
    "AWS_STATIC_KEY_PAIR_ENV",
]
