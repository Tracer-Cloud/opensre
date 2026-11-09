"""Shared Jenkins integration helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import Field, field_validator

from config.constants.jenkins import (
    JENKINS_API_TOKEN_ENV,
    JENKINS_BASE_URL_ENV,
    JENKINS_USERNAME_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)


class JenkinsConfig(StrictConfigModel):
    """Normalized Jenkins connection settings."""

    base_url: str = ""
    username: str = ""
    api_token: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        return str(value or "").strip()

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        # Jenkins Basic auth sends username:api_token — an empty username yields
        # a ":token" pair that Jenkins rejects with 401, so require all three.
        return bool(self.base_url and self.username and self.api_token)

    @property
    def auth(self) -> tuple[str, str]:
        """Jenkins authenticates with HTTP Basic auth: (username, api_token)."""
        return (self.username, self.api_token)


@dataclass(frozen=True)
class JenkinsValidationResult:
    """Result of validating a Jenkins integration."""

    ok: bool
    detail: str


def build_jenkins_config(raw: dict[str, Any] | None) -> JenkinsConfig:
    """Build a normalized Jenkins config object from env/store data."""
    return JenkinsConfig.model_validate(raw or {})


def jenkins_config_from_env() -> JenkinsConfig | None:
    """Load a Jenkins config from env vars."""
    base_url = os.getenv(JENKINS_BASE_URL_ENV, "").strip()
    api_token = resolve_env_credential(JENKINS_API_TOKEN_ENV)
    if not base_url or not api_token:
        return None
    return build_jenkins_config(
        {
            "base_url": base_url,
            "username": os.getenv(JENKINS_USERNAME_ENV, "").strip(),
            "api_token": api_token,
        }
    )


def _request_json(
    config: JenkinsConfig,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int | float | bool | None]] | None = None,
) -> Any:
    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        auth=config.auth,
        params=params,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def validate_jenkins_config(config: JenkinsConfig) -> JenkinsValidationResult:
    """Validate Jenkins connectivity with a lightweight server-info query."""

    if not config.base_url:
        return JenkinsValidationResult(ok=False, detail="Jenkins base URL is required.")
    if not config.api_base_url.startswith(("http://", "https://")):
        return JenkinsValidationResult(
            ok=False, detail="Jenkins base URL must start with http:// or https://."
        )
    if not config.username:
        return JenkinsValidationResult(ok=False, detail="Jenkins username is required.")
    if not config.api_token:
        return JenkinsValidationResult(ok=False, detail="Jenkins API token is required.")

    try:
        payload = _request_json(config, "GET", "/api/json")
        node = payload.get("nodeName", "") if isinstance(payload, dict) else ""
        node_label = node or "built-in"
        return JenkinsValidationResult(
            ok=True,
            detail=f"Jenkins connectivity successful at {config.api_base_url} (node: {node_label})",
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return JenkinsValidationResult(ok=False, detail=f"Jenkins validation failed: {detail}")
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="jenkins",
            method="validate_jenkins_config",
        )
        return JenkinsValidationResult(ok=False, detail=f"Jenkins validation failed: {err}")


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[JenkinsConfig | None, str | None]:
    try:
        cfg = build_jenkins_config(
            {
                "base_url": credentials.get("base_url", ""),
                "username": credentials.get("username", ""),
                "api_token": credentials.get("api_token", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="jenkins", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "jenkins"
    return None, None
