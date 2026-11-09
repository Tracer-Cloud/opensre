"""Configuration normalization and catalog classification for Bitbucket."""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import Field, field_validator

from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)

DEFAULT_BITBUCKET_BASE_URL = "https://api.bitbucket.org/2.0"
DEFAULT_BITBUCKET_TIMEOUT_SECONDS = 10.0
DEFAULT_BITBUCKET_MAX_RESULTS = 25


class BitbucketConfig(StrictConfigModel):
    """Normalized Bitbucket connection settings."""

    workspace: str = ""
    app_password: str = ""
    username: str = ""
    base_url: str = DEFAULT_BITBUCKET_BASE_URL
    timeout_seconds: float = Field(default=DEFAULT_BITBUCKET_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_BITBUCKET_MAX_RESULTS, gt=0, le=100)
    integration_id: str = ""

    @field_validator("workspace", mode="before")
    @classmethod
    def _normalize_workspace(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_BITBUCKET_BASE_URL).strip().rstrip("/")
        return normalized or DEFAULT_BITBUCKET_BASE_URL

    @property
    def is_configured(self) -> bool:
        return bool(self.workspace and self.app_password and self.username)


def build_bitbucket_config(raw: dict[str, Any] | None) -> BitbucketConfig:
    """Build a normalized Bitbucket config object from env/store data."""
    return BitbucketConfig.model_validate(raw or {})


def bitbucket_config_from_env() -> BitbucketConfig | None:
    """Load a Bitbucket config from env vars."""
    workspace = os.getenv("BITBUCKET_WORKSPACE", "").strip()
    if not workspace:
        return None
    return build_bitbucket_config(
        {
            "workspace": workspace,
            "username": os.getenv("BITBUCKET_USERNAME", "").strip(),
            "app_password": os.getenv("BITBUCKET_APP_PASSWORD", "").strip(),
            "base_url": os.getenv("BITBUCKET_BASE_URL", DEFAULT_BITBUCKET_BASE_URL).strip(),
        }
    )


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[BitbucketConfig | None, str | None]:
    """Classify stored credentials as a normalized Bitbucket configuration."""
    try:
        config = BitbucketConfig.model_validate(
            {
                "workspace": credentials.get("workspace", ""),
                "username": credentials.get("username", ""),
                "app_password": credentials.get("app_password", ""),
                "base_url": credentials.get("base_url", DEFAULT_BITBUCKET_BASE_URL),
                "max_results": credentials.get("max_results", DEFAULT_BITBUCKET_MAX_RESULTS),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="bitbucket", record_id=record_id)
        return None, None
    if config.workspace:
        return config, "bitbucket"
    return None, None
