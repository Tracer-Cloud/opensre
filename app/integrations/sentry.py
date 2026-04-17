"""Shared Sentry integration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_SENTRY_URL = "https://sentry.io"
DEFAULT_SENTRY_STATS_PERIOD = "24h"


class SentryConfig(StrictConfigModel):
    """Normalized Sentry connection settings."""

    base_url: str = DEFAULT_SENTRY_URL
    organization_slug: str = ""
    auth_token: str = ""
    project_slug: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_SENTRY_URL).strip()
        return normalized or DEFAULT_SENTRY_URL

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth_token}",
            "Accept": "application/json",
        }


@dataclass(frozen=True)
class SentryValidationResult:
    """Result of validating a Sentry integration."""

    ok: bool
    detail: str
    issue_count: int = 0


def build_sentry_config(raw: dict[str, Any] | None) -> SentryConfig:
    """Build a normalized Sentry config object from env/store data."""
    return SentryConfig.model_validate(raw or {})


def sentry_config_from_env() -> SentryConfig | None:
    """Load a Sentry config from env vars."""
    organization_slug = os.getenv("SENTRY_ORG_SLUG", "").strip()
    auth_token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    if not organization_slug or not auth_token:
        return None
    return build_sentry_config({
        "base_url": os.getenv("SENTRY_URL", DEFAULT_SENTRY_URL).strip() or DEFAULT_SENTRY_URL,
        "organization_slug": organization_slug,
        "auth_token": auth_token,
        "project_slug": os.getenv("SENTRY_PROJECT_SLUG", "").strip(),
    })


def get_sentry_auth_recommendations() -> dict[str, str]:
    """Return operator guidance for creating the right Sentry token."""
    return {
        "recommended_token_type": "Organization Token",
        "why": (
            "Use an Organization Token first for least-privilege automation. "
            "Use an Internal Integration only if you need broader organization-level API scopes."
        ),
        "where_to_create": "Settings > Developer Settings > Organization Tokens",
        "fallback_token_type": "Internal Integration",
        "fallback_where_to_create": "Settings > Developer Settings > Internal Integrations",
        "required_scope_hint": "Issue and event lookup requires an auth token with event:read access.",
    }


def _build_issue_list_params(
    config: SentryConfig,
    limit: int,
    query: str,
) -> list[tuple[str, str | int | float | bool | None]]:
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("limit", str(limit)),
        ("statsPeriod", DEFAULT_SENTRY_STATS_PERIOD),
        ("query", query),
    ]
    if config.project_slug:
        params.append(("project", config.project_slug))
    return params


def _request_json(
    config: SentryConfig,
    method: str,
    path: str,
    *,
    params: list[tuple[str, str | int | float | bool | None]] | None = None,
) -> Any:
    url = f"{config.api_base_url}{path}"
    response = httpx.request(
        method,
        url,
        headers=config.auth_headers,
        params=params,
        timeout=config.timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def validate_sentry_config(config: SentryConfig) -> SentryValidationResult:
    """Validate Sentry connectivity with a lightweight issues query."""

    if not config.organization_slug:
        return SentryValidationResult(ok=False, detail="Sentry organization slug is required.")
    if not config.auth_token:
        return SentryValidationResult(ok=False, detail="Sentry auth token is required.")

    try:
        issues = list_sentry_issues(config=config, limit=1)
        issue_count = len(issues)
        return SentryValidationResult(
            ok=True,
            detail=(
                f"Sentry validated for org {config.organization_slug}; "
                f"issues API responded successfully with {issue_count} issue(s)."
            ),
            issue_count=issue_count,
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return SentryValidationResult(ok=False, detail=f"Sentry validation failed: {detail}")
    except Exception as err:  # noqa: BLE001
        return SentryValidationResult(ok=False, detail=f"Sentry validation failed: {err}")


def list_sentry_issues(
    *,
    config: SentryConfig,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List Sentry issues for an organization."""

    payload = _request_json(
        config,
        "GET",
        f"/api/0/organizations/{config.organization_slug}/issues/",
        params=_build_issue_list_params(config, limit, query),
    )
    return payload if isinstance(payload, list) else []


def get_sentry_issue(
    *,
    config: SentryConfig,
    issue_id: str,
) -> dict[str, Any]:
    """Fetch full details for one Sentry issue."""

    payload = _request_json(
        config,
        "GET",
        f"/api/0/organizations/{config.organization_slug}/issues/{issue_id}/",
    )
    return payload if isinstance(payload, dict) else {}


def list_sentry_issue_events(
    *,
    config: SentryConfig,
    issue_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List recent events for a Sentry issue."""

    payload = _request_json(
        config,
        "GET",
        f"/api/0/organizations/{config.organization_slug}/issues/{issue_id}/events/",
        params=[("limit", str(limit))],
    )
    return payload if isinstance(payload, list) else []
