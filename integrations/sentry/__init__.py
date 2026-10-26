"""Shared Sentry integration helpers."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

import integrations.sentry.client as _sentry_client
from config.constants.sentry import (
    DEFAULT_SENTRY_BASE_URL,
    SENTRY_AUTH_TOKEN_ENV,
    SENTRY_BASE_URL_ENV,
    SENTRY_ORGANIZATION_SLUG_ENV,
    SENTRY_PROJECT_SLUG_ENV,
    SENTRY_STATS_PERIOD_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from integrations._validation_helpers import report_classify_failure, report_validation_failure

logger = logging.getLogger(__name__)

DEFAULT_SENTRY_URL = DEFAULT_SENTRY_BASE_URL
DEFAULT_SENTRY_STATS_PERIOD = "24h"
# Sentry's issues endpoint caps the page size at 100; asking for more is
# silently truncated to 100. We default to the full page so a search returns
# the whole recent issue set instead of a tiny slice (a low limit was why
# queries appeared to "only find one issue").
DEFAULT_SENTRY_ISSUE_LIMIT = 100
_MAX_SENTRY_PAGE_SIZE = 100
_MAX_SENTRY_QUERY_LEN = 200
_OR_SPLIT = re.compile(r"\s+OR\s+", re.IGNORECASE)
# Window used by the verification probe to report a recent issue count.
_SENTRY_VERIFY_STATS_PERIOD = "7d"
_SENTRY_VERIFY_WINDOW_LABEL = "last 7 days"


def _resolve_stats_period(explicit: str | None = None) -> str:
    """Resolve the issues lookback window, overridable via ``SENTRY_STATS_PERIOD``."""
    period = (explicit or os.getenv(SENTRY_STATS_PERIOD_ENV, "") or "").strip()
    return period or DEFAULT_SENTRY_STATS_PERIOD


def _clamp_issue_limit(limit: int | None) -> int:
    """Clamp a requested issue limit into Sentry's valid 1..100 page range."""
    try:
        value = DEFAULT_SENTRY_ISSUE_LIMIT if limit is None else int(limit)
    except (TypeError, ValueError):
        value = DEFAULT_SENTRY_ISSUE_LIMIT
    return max(1, min(value, _MAX_SENTRY_PAGE_SIZE))


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
    organization_slug = os.getenv(SENTRY_ORGANIZATION_SLUG_ENV, "").strip()
    auth_token = resolve_env_credential(SENTRY_AUTH_TOKEN_ENV)
    if not organization_slug or not auth_token:
        return None
    return build_sentry_config(
        {
            "base_url": os.getenv(SENTRY_BASE_URL_ENV, DEFAULT_SENTRY_URL).strip()
            or DEFAULT_SENTRY_URL,
            "organization_slug": organization_slug,
            "auth_token": auth_token,
            "project_slug": os.getenv(SENTRY_PROJECT_SLUG_ENV, "").strip(),
        }
    )


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
        "required_scope_hint": (
            "Issue and event lookup requires event:read. "
            "Uptime monitor watching (opensre sentry uptime) also needs alerts:read."
        ),
    }


def _normalize_sentry_query_segment(segment: str) -> str:
    return segment.strip()[:_MAX_SENTRY_QUERY_LEN]


def _sentry_query_candidates(query: str) -> list[str]:
    """Return ordered issue-search query strings to try against the Sentry API.

    Issue search does not support ``OR`` (unlike Discover). When the agent
    passes ``"foo" OR "bar"``, each alternative is returned so callers can
    retry after a 400 ``InvalidSearchQuery`` response.
    """
    first_line = query.split("\n")[0].strip()
    if not first_line:
        return [""]
    if _OR_SPLIT.search(first_line):
        segments = [
            _normalize_sentry_query_segment(part)
            for part in _OR_SPLIT.split(first_line)
            if part.strip()
        ]
        return segments or [""]
    return [_normalize_sentry_query_segment(first_line)]


def _sanitize_sentry_query(query: str) -> str:
    """Reduce a raw query string to something the Sentry issues API accepts.

    The agent may pass a full error message or multi-line stack trace as the
    search term, which causes a 400 Bad Request because the Sentry search
    grammar treats ``:`` as a field separator and rejects very long URLs.
    Taking the first non-empty line and capping at _MAX_SENTRY_QUERY_LEN
    characters is enough to produce a valid free-text search token.
    """
    return _sentry_query_candidates(query)[0]


def describe_sentry_api_error(
    err: _sentry_client.HTTPStatusError,
    *,
    query: str = "",
    project_slug: str = "",
) -> str:
    """Turn a Sentry HTTP failure into an operator- and agent-friendly message."""
    return _sentry_client.describe_sentry_api_error(
        err,
        query_has_or=bool(_OR_SPLIT.search(query.split("\n", maxsplit=1)[0])),
        project_slug=project_slug,
    )


def _build_issue_list_params(
    config: SentryConfig,
    limit: int,
    query: str,
    stats_period: str | None = None,
    *,
    normalized_query: str | None = None,
) -> list[tuple[str, str | int | float | bool | None]]:
    effective_query = (
        normalized_query if normalized_query is not None else _sanitize_sentry_query(query)
    )
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("limit", str(_clamp_issue_limit(limit))),
        ("statsPeriod", _resolve_stats_period(stats_period)),
        ("query", effective_query),
    ]
    if config.project_slug:
        params.append(("project", config.project_slug))
    return params


def validate_sentry_config(config: SentryConfig) -> SentryValidationResult:
    """Validate Sentry connectivity with a lightweight issues query."""

    if not config.organization_slug:
        return SentryValidationResult(ok=False, detail="Sentry organization slug is required.")
    if not config.auth_token:
        return SentryValidationResult(ok=False, detail="Sentry auth token is required.")

    try:
        # Fetch a full page over the verify window so the detail reports a
        # meaningful recent issue count instead of a probe artifact. The count
        # is capped at the Sentry page size, shown as "N+" when it saturates.
        issues = list_sentry_issues(
            config=config,
            limit=DEFAULT_SENTRY_ISSUE_LIMIT,
            stats_period=_SENTRY_VERIFY_STATS_PERIOD,
        )
        issue_count = len(issues)
        count_label = (
            f"{issue_count}+" if issue_count >= _MAX_SENTRY_PAGE_SIZE else str(issue_count)
        )
        return SentryValidationResult(
            ok=True,
            detail=(
                f"Sentry validated for org {config.organization_slug}; "
                f"{count_label} issue(s) in the {_SENTRY_VERIFY_WINDOW_LABEL}."
            ),
            issue_count=issue_count,
        )
    except _sentry_client.HTTPStatusError as err:
        detail = err.response.text.strip() or str(err)
        return SentryValidationResult(ok=False, detail=f"Sentry validation failed: {detail}")
    except Exception as err:
        report_validation_failure(
            err,
            logger=logger,
            integration="sentry",
            method="validate_sentry_config",
        )
        return SentryValidationResult(ok=False, detail=f"Sentry validation failed: {err}")


def list_sentry_issues(
    *,
    config: SentryConfig,
    query: str = "",
    limit: int = DEFAULT_SENTRY_ISSUE_LIMIT,
    stats_period: str | None = None,
) -> list[dict[str, Any]]:
    """List Sentry issues for an organization.

    ``limit`` is clamped to Sentry's 1..100 page range; ``stats_period``
    (e.g. ``24h``, ``14d``) defaults to ``SENTRY_STATS_PERIOD`` then ``24h``.
    """

    params_by_candidate = (
        _build_issue_list_params(
            config,
            limit,
            query,
            stats_period,
            normalized_query=candidate,
        )
        for candidate in _sentry_query_candidates(query)
    )
    return _sentry_client.list_sentry_issues(
        config=config,
        params_by_candidate=params_by_candidate,
    )


def get_sentry_issue(
    *,
    config: SentryConfig,
    issue_id: str,
) -> dict[str, Any]:
    """Fetch full details for one Sentry issue."""

    return _sentry_client.get_sentry_issue(config=config, issue_id=issue_id)


def list_sentry_issue_events(
    *,
    config: SentryConfig,
    issue_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List recent events for a Sentry issue."""

    return _sentry_client.list_sentry_issue_events(
        config=config,
        issue_id=issue_id,
        limit=limit,
    )


def classify(credentials: dict[str, Any], record_id: str) -> tuple[SentryConfig | None, str | None]:
    try:
        cfg = build_sentry_config(
            {
                "base_url": credentials.get("base_url", "https://sentry.io"),
                "organization_slug": credentials.get("organization_slug", ""),
                "auth_token": credentials.get("auth_token", ""),
                "project_slug": credentials.get("project_slug", ""),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="sentry", record_id=record_id)
        return None, None
    if cfg.organization_slug and cfg.auth_token:
        return cfg, "sentry"
    return None, None


__all__ = [
    "DEFAULT_SENTRY_ISSUE_LIMIT",
    "DEFAULT_SENTRY_STATS_PERIOD",
    "DEFAULT_SENTRY_URL",
    "SentryConfig",
    "SentryValidationResult",
    "build_sentry_config",
    "classify",
    "describe_sentry_api_error",
    "get_sentry_auth_recommendations",
    "get_sentry_issue",
    "list_sentry_issue_events",
    "list_sentry_issues",
    "sentry_config_from_env",
    "validate_sentry_config",
]
