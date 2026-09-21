"""Remote validation of a stored OpenSRE account session.

A signed-in laptop persists account metadata locally; the webapp is the only
authority on whether that login still works. Both the user-facing ``account
status`` command and hosted-LLM-route selection need the same request, so the
HTTP call lives here rather than in a surface.
"""

from __future__ import annotations

from enum import StrEnum
from http import HTTPStatus

import httpx

from config.constants.account import (
    OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
    OPENSRE_ACCOUNT_SESSION_PATH,
)


class RemoteSessionState(StrEnum):
    """What the webapp says about a stored login."""

    VALID = "valid"
    INVALID = "invalid"  # 401: expired or revoked
    UNAVAILABLE = "unavailable"  # unreachable, or a non-200 response


def fetch_account_session(
    app_url: str,
    token: str,
    *,
    timeout: float = OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
) -> httpx.Response | None:
    """GET the session endpoint, or ``None`` when the webapp is unreachable."""
    try:
        return httpx.get(
            f"{app_url}{OPENSRE_ACCOUNT_SESSION_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return None


def validate_remote_session(
    app_url: str,
    token: str,
    *,
    timeout: float = OPENSRE_ACCOUNT_HTTP_TIMEOUT_SECONDS,
) -> RemoteSessionState:
    """Validate ``token`` against ``app_url`` without interpreting the body."""
    response = fetch_account_session(app_url, token, timeout=timeout)
    if response is None:
        return RemoteSessionState.UNAVAILABLE
    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return RemoteSessionState.INVALID
    if response.status_code != HTTPStatus.OK:
        return RemoteSessionState.UNAVAILABLE
    return RemoteSessionState.VALID


__all__ = ["RemoteSessionState", "fetch_account_session", "validate_remote_session"]
