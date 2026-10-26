"""Sentry HTTP transport and issue operations."""

from __future__ import annotations

from collections.abc import Iterable
from http import HTTPStatus
from typing import Any, Protocol

import httpx
from httpx import HTTPStatusError


class _SentryRequestConfig(Protocol):
    """Connection settings required by the Sentry HTTP transport."""

    organization_slug: str
    project_slug: str
    timeout_seconds: float

    @property
    def api_base_url(self) -> str:
        """Return the normalized Sentry API base URL."""

    @property
    def auth_headers(self) -> dict[str, str]:
        """Return the headers used to authenticate Sentry requests."""


def describe_sentry_api_error(
    err: HTTPStatusError,
    *,
    query_has_or: bool = False,
    project_slug: str = "",
) -> str:
    """Turn a Sentry HTTP failure into an operator- and agent-friendly message."""
    detail = ""
    try:
        body = err.response.json()
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or "").strip()
    except Exception:
        detail = err.response.text.strip()
    if not detail:
        detail = str(err)

    hints: list[str] = []
    if err.response.status_code == HTTPStatus.BAD_REQUEST:
        if query_has_or:
            hints.append(
                "Sentry issue search does not support OR; use one keyword or phrase at a time."
            )
        if project_slug:
            hints.append(f"Verify project slug {project_slug!r} exists in the organization.")
        hints.append(
            "Prefer short free-text keywords or field filters such as is:unresolved level:error."
        )

    message = f"Sentry API returned HTTP {err.response.status_code}: {detail}"
    if hints:
        message = f"{message} {' '.join(hints)}"
    return message


def _request_json(
    config: _SentryRequestConfig,
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


def list_sentry_issues(
    *,
    config: _SentryRequestConfig,
    params_by_candidate: Iterable[list[tuple[str, str | int | float | bool | None]]],
) -> list[dict[str, Any]]:
    """List Sentry issues, retrying alternate query candidates after bad requests."""
    path = f"/api/0/organizations/{config.organization_slug}/issues/"
    last_error: HTTPStatusError | None = None
    for params in params_by_candidate:
        try:
            payload = _request_json(config, "GET", path, params=params)
            return payload if isinstance(payload, list) else []
        except HTTPStatusError as err:
            if err.response.status_code == HTTPStatus.BAD_REQUEST:
                last_error = err
                continue
            raise
    if last_error is not None:
        raise last_error
    return []


def get_sentry_issue(
    *,
    config: _SentryRequestConfig,
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
    config: _SentryRequestConfig,
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


__all__ = [
    "describe_sentry_api_error",
    "get_sentry_issue",
    "list_sentry_issue_events",
    "list_sentry_issues",
]
