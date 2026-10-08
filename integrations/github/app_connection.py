"""App setup and fresh credential resolution for GitHub operations."""

from __future__ import annotations

import os
from typing import Any

from config.account import is_secure_account_origin, normalize_account_app_url
from config.constants.account import OPENSRE_APP_URL_DEFAULT, OPENSRE_APP_URL_ENV
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.github import GITHUB_CONNECTION_ID_PARAM
from config.constants.skills import REPORTING_GITHUB_CI_FAILURES_SKILL_NAME
from infrastructure.harness_providers import current_github_connection_id
from integrations.account_integrations import account_setup_url, load_account_integrations
from integrations.github.rest_token import resolved_github_rest_token


def github_setup_url() -> str:
    """Return the account setup page, or a validated host app page when signed out."""
    account_url = account_setup_url()
    if account_url:
        return account_url
    candidate = (
        os.getenv(WEBAPP_URL_ENV) or os.getenv(OPENSRE_APP_URL_ENV) or OPENSRE_APP_URL_DEFAULT
    )
    try:
        origin = normalize_account_app_url(candidate)
        if not is_secure_account_origin(origin):
            origin = OPENSRE_APP_URL_DEFAULT
    except ValueError:
        origin = OPENSRE_APP_URL_DEFAULT
    return f"{origin}/home"


def _refreshed_github_selection(connection_id: str | None) -> dict[str, Any]:
    from integrations.catalog import classify_integrations
    from integrations.github.connections import select_github_connection
    from integrations.webapp_vault import fetch_webapp_org_integrations, webapp_vault_configured

    records = (
        fetch_webapp_org_integrations()
        if webapp_vault_configured()
        else load_account_integrations(refresh=True)
    )
    resolved = classify_integrations(records or [])
    if connection_id and not any(
        grant.get("connection_id") == connection_id
        for grant in resolved.get("_all_github_instances", [])
    ):
        return {}
    return select_github_connection(resolved, connection_id)


def refreshed_github_token(connection_id: str | None = None) -> str:
    """Re-resolve an app grant for background work without ambient credentials."""
    return resolved_github_rest_token(_refreshed_github_selection(connection_id))


def _github_schedule_connection_id(connection_id: str | None) -> str:
    """Pin the effective app grant, retaining unavailable explicit choices for blocked refresh."""
    selected = _refreshed_github_selection(connection_id)
    if resolved_github_rest_token(selected):
        return str(selected.get("github", {}).get("connection_id") or connection_id or "")
    return connection_id or ""


def github_schedule_inputs(
    skill_name: str, inputs: dict[str, str], connection_id: str = ""
) -> dict[str, str]:
    """Bind CI reports to their app grant and reject that option on other schedules."""
    if skill_name != REPORTING_GITHUB_CI_FAILURES_SKILL_NAME:
        if connection_id:
            raise ValueError(
                "--github-connection-id is only valid for reporting-github-ci-failures."
            )
        return inputs
    selected = _github_schedule_connection_id(
        connection_id.strip() or current_github_connection_id()
    )
    return {**inputs, GITHUB_CONNECTION_ID_PARAM: selected} if selected else inputs


__all__ = [
    "github_schedule_inputs",
    "github_setup_url",
    "refreshed_github_token",
]
