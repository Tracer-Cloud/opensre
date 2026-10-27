"""Local configuration hints for ambient auth; never fetch credentials here."""

from __future__ import annotations

import os
from pathlib import Path


def ambient_configured(provider: str) -> bool:
    checks = {"bedrock": _aws_configured, "vertex-ai": _gcp_configured}
    check = checks.get(provider)
    return check() if check else False


def _aws_configured() -> bool:
    return (
        any(
            os.getenv(key)
            for key in (
                "AWS_PROFILE",
                "AWS_ACCESS_KEY_ID",
                "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
                "AWS_WEB_IDENTITY_TOKEN_FILE",
            )
        )
        or (Path.home() / ".aws" / "credentials").exists()
    )


def _gcp_configured() -> bool:
    return (
        bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("VERTEX_AI_PROJECT"))
        or (Path.home() / ".config" / "gcloud" / "application_default_credentials.json").exists()
    )
