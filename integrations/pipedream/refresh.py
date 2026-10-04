"""Refresh a Pipedream access token from the OpenSRE app before it expires."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

_SKEW = timedelta(seconds=60)


def token_is_fresh(expires_at: str) -> bool:
    """True when ``expires_at`` is an ISO timestamp still outside the skew window."""
    text = expires_at.strip()
    if not text:
        return False
    try:
        expires = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > datetime.now(UTC) + _SKEW


def refresh_pipedream_source(source: dict[str, Any]) -> dict[str, Any]:
    """Return ``source`` with a current access token.

    The hosted agent refreshes through the fleet vault. The interactive shell
    refreshes through the signed-in account route. A failed refresh keeps the
    token already in hand so a transient app outage does not drop a still-valid
    credential.
    """
    if token_is_fresh(str(source.get("token_expires_at") or "")):
        return source

    credentials = _fetch_credentials()
    if not credentials:
        return source
    token = str(credentials.get("auth_token") or "").strip()
    if not token:
        return source
    refreshed = dict(source)
    refreshed["auth_token"] = token
    refreshed["token_expires_at"] = str(credentials.get("token_expires_at") or "")
    if credentials.get("apps"):
        refreshed["apps"] = credentials["apps"]
    return refreshed


def _fetch_credentials() -> dict[str, str] | None:
    from integrations.webapp_vault import (
        fetch_webapp_org_integrations,
        webapp_vault_configured,
    )

    records = fetch_webapp_org_integrations() if webapp_vault_configured() else None
    if records is None:
        from integrations.account_integrations import load_account_integrations

        records = load_account_integrations(refresh=True)
    if not records:
        return None
    for record in records:
        if str(record.get("service") or "").strip() != "pipedream":
            continue
        credentials = _record_credentials(record)
        if isinstance(credentials, dict) and credentials:
            return {str(key): str(value) for key, value in credentials.items()}
    return None


def _record_credentials(record: dict[str, Any]) -> dict[str, Any] | None:
    credentials = record.get("credentials")
    if isinstance(credentials, dict):
        return credentials
    instances = record.get("instances")
    if not isinstance(instances, list):
        return None
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        credentials = instance.get("credentials")
        if isinstance(credentials, dict):
            return credentials
    return None
