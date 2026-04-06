"""Shared PostHog integration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_POSTHOG_URL = "https://us.i.posthog.com"
DEFAULT_POSTHOG_BOUNCE_THRESHOLD = 0.6
DEFAULT_POSTHOG_BOUNCE_WINDOW = "24h"


class PostHogConfig(StrictConfigModel):
    """Normalized PostHog connection settings."""

    base_url: str = DEFAULT_POSTHOG_URL
    project_id: str = ""
    personal_api_key: str = ""
    timeout_seconds: float = Field(default=15.0, gt=0)
    bounce_rate_threshold: float = Field(default=DEFAULT_POSTHOG_BOUNCE_THRESHOLD, ge=0.0, le=1.0)
    bounce_rate_window: str = DEFAULT_POSTHOG_BOUNCE_WINDOW
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_POSTHOG_URL).strip()
        return normalized or DEFAULT_POSTHOG_URL

    @field_validator("project_id", mode="before")
    @classmethod
    def _normalize_project_id(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("personal_api_key", mode="before")
    @classmethod
    def _normalize_personal_api_key(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("bounce_rate_window", mode="before")
    @classmethod
    def _normalize_bounce_rate_window(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_POSTHOG_BOUNCE_WINDOW).strip()
        return normalized or DEFAULT_POSTHOG_BOUNCE_WINDOW

    @property
    def api_base_url(self) -> str:
        return self.base_url.rstrip("/")

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.personal_api_key}",
            "Accept": "application/json",
        }


@dataclass(frozen=True)
class PostHogValidationResult:
    """Result of validating a PostHog integration."""

    ok: bool
    detail: str


@dataclass(frozen=True)
class BounceRateResult:
    """Computed bounce-rate snapshot."""

    bounce_rate: float
    total_sessions: int
    bounced_sessions: int
    period: str
    queried_at: datetime


@dataclass(frozen=True)
class BounceRateAlert:
    """Alert emitted when bounce rate exceeds the configured threshold."""

    bounce_rate: float
    threshold: float
    total_sessions: int
    bounced_sessions: int
    period: str
    severity: str
    message: str


def build_posthog_config(raw: dict[str, Any] | None) -> PostHogConfig:
    """Build a normalized PostHog config object from env/store data."""
    return PostHogConfig.model_validate(raw or {})


def posthog_config_from_env() -> PostHogConfig | None:
    """Load a PostHog config from env vars."""
    project_id = os.getenv("POSTHOG_PROJECT_ID", "").strip()
    personal_api_key = os.getenv("POSTHOG_PERSONAL_API_KEY", "").strip()

    if not project_id or not personal_api_key:
        return None

    return build_posthog_config(
        {
            "base_url": os.getenv("POSTHOG_BASE_URL", DEFAULT_POSTHOG_URL).strip() or DEFAULT_POSTHOG_URL,
            "project_id": project_id,
            "personal_api_key": personal_api_key,
            "timeout_seconds": float(os.getenv("POSTHOG_TIMEOUT_SECONDS", "15.0")),
            "bounce_rate_threshold": float(
                os.getenv("POSTHOG_BOUNCE_THRESHOLD", str(DEFAULT_POSTHOG_BOUNCE_THRESHOLD))
            ),
            "bounce_rate_window": os.getenv("POSTHOG_BOUNCE_WINDOW", DEFAULT_POSTHOG_BOUNCE_WINDOW).strip()
            or DEFAULT_POSTHOG_BOUNCE_WINDOW,
        }
    )


def _request_json(
    config: PostHogConfig,
    method: str,
    path: str,
    *,
    params: dict[str, str | int | float] | None = None,
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


def validate_posthog_config(config: PostHogConfig) -> PostHogValidationResult:
    """Validate PostHog connectivity with a lightweight project lookup."""

    if not config.project_id:
        return PostHogValidationResult(ok=False, detail="PostHog project ID is required.")
    if not config.personal_api_key:
        return PostHogValidationResult(ok=False, detail="PostHog personal API key is required.")

    try:
        payload = _request_json(
            config,
            "GET",
            f"/api/projects/{config.project_id}/",
        )
        name = payload.get("name", "") if isinstance(payload, dict) else ""
        suffix = f" ({name})" if name else ""
        return PostHogValidationResult(
            ok=True,
            detail=f"PostHog validated for project {config.project_id}{suffix}.",
        )
    except httpx.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return PostHogValidationResult(ok=False, detail=f"PostHog validation failed: {detail}")
    except Exception as err:  # noqa: BLE001
        return PostHogValidationResult(ok=False, detail=f"PostHog validation failed: {err}")


def query_bounce_rate(
    config: PostHogConfig,
    *,
    period: str = DEFAULT_POSTHOG_BOUNCE_WINDOW,
) -> BounceRateResult:
    """Query PostHog and compute bounce rate for the requested period.

    Expected mocked response shape:
    {
        "total_sessions": 1000,
        "bounced_sessions": 750
    }
    """

    payload = _request_json(
        config,
        "GET",
        f"/api/projects/{config.project_id}/insights/trend/",
        params={
            "event": "$pageview",
            "date_from": f"-{period}",
        },
    )

    if not isinstance(payload, dict):
        raise ValueError("Unexpected PostHog response shape.")

    total_sessions = int(payload.get("total_sessions", 0))
    bounced_sessions = int(payload.get("bounced_sessions", 0))

    bounce_rate = 0.0
    if total_sessions > 0:
        bounce_rate = bounced_sessions / total_sessions

    return BounceRateResult(
        bounce_rate=bounce_rate,
        total_sessions=total_sessions,
        bounced_sessions=bounced_sessions,
        period=period,
        queried_at=datetime.now(datetime.UTC),
    )


def check_bounce_rate_alert(config: PostHogConfig) -> BounceRateAlert | None:
    """Return an alert object when bounce rate exceeds the configured threshold."""

    result = query_bounce_rate(config, period=config.bounce_rate_window)

    if result.bounce_rate <= config.bounce_rate_threshold:
        return None

    severity = "critical" if result.bounce_rate > 0.9 else "warning"
    bounce_pct = round(result.bounce_rate * 100, 1)
    threshold_pct = round(config.bounce_rate_threshold * 100, 1)

    return BounceRateAlert(
        bounce_rate=result.bounce_rate,
        threshold=config.bounce_rate_threshold,
        total_sessions=result.total_sessions,
        bounced_sessions=result.bounced_sessions,
        period=result.period,
        severity=severity,
        message=(
            f"Bounce rate is {bounce_pct}% over the last {result.period}, "
            f"above the configured threshold of {threshold_pct}%."
        ),
    )
