"""Local configuration hints for ambient auth; never fetch credentials here."""

from __future__ import annotations

import os
from pathlib import Path

from config.constants.aws import (
    AWS_ACCESS_KEY_ID_ENV,
    AWS_CONTAINER_CREDENTIALS_RELATIVE_URI_ENV,
    AWS_PROFILE_ENV,
    AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
)
from config.constants.gcp import GOOGLE_APPLICATION_CREDENTIALS_ENV, VERTEX_AI_PROJECT_ENV


def ambient_configured(provider: str) -> bool:
    checks = {"bedrock": _aws_configured, "vertex-ai": _gcp_configured}
    check = checks.get(provider)
    return check() if check else False


def _aws_configured() -> bool:
    return (
        any(
            os.getenv(key)
            for key in (
                AWS_PROFILE_ENV,
                AWS_ACCESS_KEY_ID_ENV,
                AWS_CONTAINER_CREDENTIALS_RELATIVE_URI_ENV,
                AWS_WEB_IDENTITY_TOKEN_FILE_ENV,
            )
        )
        or (Path.home() / ".aws" / "credentials").exists()
    )


def _gcp_configured() -> bool:
    return (
        bool(os.getenv(GOOGLE_APPLICATION_CREDENTIALS_ENV) or os.getenv(VERTEX_AI_PROJECT_ENV))
        or (Path.home() / ".config" / "gcloud" / "application_default_credentials.json").exists()
    )
