"""Resolve configured credentials afresh in the background worker."""

from __future__ import annotations

from collections.abc import Mapping

from integrations.github.app_connection import refreshed_github_token


def configured_token(explicit: str | None = None) -> str:
    """Use a vetted injected token or freshly resolve the app connection."""
    token = effective_github_token(explicit)
    if token:
        return token
    raise ValueError("Connect or reconnect GitHub in the OpenSRE app before scheduling.")


def effective_github_token(explicit: str | None = None) -> str:
    """Use an explicit token without fallback, or re-resolve an app grant."""
    return explicit.strip() if explicit is not None else refreshed_github_token()


def account_id(user: Mapping[str, object]) -> int:
    """Require the stable GitHub account ID before authorizing a durable run."""
    value = user.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("GitHub did not return a valid account identity.")
    return value
