"""Neon integration config, env loading, and store classification.

One record scopes OpenSRE to a single Neon project. ``branch_id`` is optional:
when it is empty, the tools resolve the project's default branch at call time,
so a promoted branch is followed without editing the integration.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import Field, field_validator

from config.constants.neon import (
    NEON_API_BASE_URL,
    NEON_API_KEY_ENV,
    NEON_BRANCH_ID_ENV,
    NEON_PROJECT_ID_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure

logger = logging.getLogger(__name__)

DEFAULT_NEON_TIMEOUT_SECONDS = 15.0
DEFAULT_NEON_MAX_RESULTS = 50


class NeonConfig(StrictConfigModel):
    """Normalized Neon API settings for one project."""

    api_key: str = ""
    project_id: str = ""
    branch_id: str = ""
    base_url: str = NEON_API_BASE_URL
    timeout_seconds: float = Field(default=DEFAULT_NEON_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_NEON_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("api_key", "project_id", "branch_id", "integration_id", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or "").strip().rstrip("/")
        return normalized or NEON_API_BASE_URL

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.project_id)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }


def build_neon_config(raw: dict[str, Any] | None) -> NeonConfig:
    """Build a normalized Neon config from env/store data."""
    return NeonConfig.model_validate(raw or {})


def neon_config_from_env() -> NeonConfig | None:
    """Load a Neon config from env vars, or ``None`` when the pair is incomplete.

    The API key goes through ``resolve_env_credential`` (env, then the local
    credentials file); the project and branch IDs are plain env reads.
    """
    api_key = resolve_env_credential(NEON_API_KEY_ENV)
    project_id = os.getenv(NEON_PROJECT_ID_ENV, "").strip()
    if not api_key or not project_id:
        return None
    return build_neon_config(
        {
            "api_key": api_key,
            "project_id": project_id,
            "branch_id": os.getenv(NEON_BRANCH_ID_ENV, ""),
        }
    )


def classify(credentials: dict[str, Any], record_id: str) -> tuple[NeonConfig | None, str | None]:
    """Turn a stored or env record into a resolved ``neon`` source."""
    try:
        config = build_neon_config(
            {
                "api_key": credentials.get("api_key", ""),
                "project_id": credentials.get("project_id", ""),
                "branch_id": credentials.get("branch_id", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="neon", record_id=record_id)
        return None, None
    if config.is_configured:
        return config, "neon"
    return None, None


__all__ = [
    "DEFAULT_NEON_MAX_RESULTS",
    "DEFAULT_NEON_TIMEOUT_SECONDS",
    "NeonConfig",
    "build_neon_config",
    "classify",
    "neon_config_from_env",
]
