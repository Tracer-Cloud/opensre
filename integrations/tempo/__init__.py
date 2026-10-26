"""Grafana Tempo integration helpers.

Provides configuration and connectivity validation for a standalone Grafana
Tempo backend via its HTTP API (``TEMPO_URL`` plus optional auth). Unlike the
Grafana Cloud integration, this talks to Tempo directly and does not require a
Grafana instance or datasource proxy.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import Field

from config.constants.tempo import (
    TEMPO_API_KEY_ENV,
    TEMPO_ORG_ID_ENV,
    TEMPO_PASSWORD_ENV,
    TEMPO_URL_ENV,
    TEMPO_USERNAME_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_TEMPO_TIMEOUT_SECONDS = 10.0
DEFAULT_TEMPO_MAX_RESULTS = 20


class TempoConfig(StrictConfigModel):
    """Normalized Grafana Tempo connection settings."""

    url: str = ""
    api_key: str = ""
    username: str = ""
    password: str = ""
    org_id: str = ""
    timeout_seconds: float = Field(default=DEFAULT_TEMPO_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_TEMPO_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @property
    def is_configured(self) -> bool:
        # Tempo commonly runs without auth behind a gateway, so a URL alone is
        # enough; auth headers are added only when credentials are present.
        return bool(self.url)

    def auth_headers(self) -> dict[str, str]:
        """Build request headers for the Tempo HTTP API."""
        headers = {"Accept": "application/json"}
        if self.username and self.password:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.org_id:
            headers["X-Scope-OrgID"] = self.org_id
        return headers

    def base_url(self) -> str:
        return self.url.rstrip("/")


@dataclass(frozen=True)
class TempoValidationResult:
    """Result of validating a Tempo integration."""

    ok: bool
    detail: str


def build_tempo_config(raw: dict[str, Any] | None) -> TempoConfig:
    """Build a normalized Tempo config object from env/store data."""
    return TempoConfig.model_validate(raw or {})


def tempo_config_from_env() -> TempoConfig | None:
    """Load a Tempo config from env vars."""
    url = os.getenv(TEMPO_URL_ENV, "").strip()
    if not url:
        return None

    return build_tempo_config(
        {
            "url": url,
            "api_key": resolve_env_credential(TEMPO_API_KEY_ENV),
            "username": os.getenv(TEMPO_USERNAME_ENV, "").strip(),
            "password": resolve_env_credential(TEMPO_PASSWORD_ENV),
            "org_id": os.getenv(TEMPO_ORG_ID_ENV, "").strip(),
        }
    )


def validate_tempo_config(config: TempoConfig) -> TempoValidationResult:
    """Validate Tempo HTTP API connectivity via the tag-search endpoint."""
    if not config.is_configured:
        return TempoValidationResult(
            ok=False,
            detail="Tempo configuration is incomplete. Provide TEMPO_URL.",
        )

    try:
        response = httpx.get(
            f"{config.base_url()}/api/search/tags",
            headers=config.auth_headers(),
            params={"limit": 1},
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        return TempoValidationResult(
            ok=True,
            detail="Connected to Grafana Tempo HTTP API (/api/search/tags).",
        )
    except httpx.HTTPStatusError as err:
        snippet = err.response.text[:200].strip()
        detail = (
            f"HTTP {err.response.status_code}: {snippet}"
            if snippet
            else f"HTTP {err.response.status_code}"
        )
        return TempoValidationResult(
            ok=False,
            detail=f"Tempo API validation failed: {detail}",
        )
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="tempo",
            method="validate_tempo_config",
        )
        return TempoValidationResult(
            ok=False,
            detail=f"Tempo API validation failed: {err}",
        )


def tempo_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Tempo connection params from resolved integrations.

    Credentials are resolved from the integration store or environment, so the
    LLM never needs to supply the URL or auth directly.
    """
    tempo = sources.get("tempo", {})
    return {
        "url": str(tempo.get("url") or "").strip(),
        "api_key": str(tempo.get("api_key") or "").strip(),
        "username": str(tempo.get("username") or "").strip(),
        "password": str(tempo.get("password") or "").strip(),
        "org_id": str(tempo.get("org_id") or "").strip(),
    }


def classify(credentials: dict[str, Any], record_id: str) -> tuple[TempoConfig | None, str | None]:
    try:
        cfg = build_tempo_config(
            {
                "url": credentials.get("url") or "",
                "api_key": credentials.get("api_key") or "",
                "username": credentials.get("username") or "",
                "password": credentials.get("password") or "",
                "org_id": credentials.get("org_id") or "",
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="tempo", record_id=record_id)
        return None, None
    if cfg.is_configured:
        return cfg, "tempo"
    return None, None
