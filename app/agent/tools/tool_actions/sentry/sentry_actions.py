"""Sentry issue and event lookup actions."""

from __future__ import annotations

from typing import Any

from app.integrations.sentry import (
    SentryConfig,
    build_sentry_config,
    get_sentry_issue,
    list_sentry_issues,
    sentry_config_from_env,
)
from app.integrations.sentry import (
    list_sentry_issue_events as sentry_list_issue_events,
)


def _resolve_config(
    sentry_url: str | None,
    organization_slug: str | None,
    sentry_token: str | None,
    project_slug: str | None = None,
) -> SentryConfig | None:
    env_config = sentry_config_from_env()
    config = build_sentry_config({
        "base_url": sentry_url or (env_config.base_url if env_config else ""),
        "organization_slug": organization_slug or (env_config.organization_slug if env_config else ""),
        "auth_token": sentry_token or (env_config.auth_token if env_config else ""),
        "project_slug": project_slug or (env_config.project_slug if env_config else ""),
    })
    if not config.organization_slug or not config.auth_token:
        return None
    return config


def search_sentry_issues(
    organization_slug: str,
    sentry_token: str,
    query: str = "",
    sentry_url: str = "",
    project_slug: str = "",
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search Sentry issues related to an incident or failure signature.

    Useful for:
    - Checking whether an alert maps to a known Sentry issue
    - Finding unresolved error groups for a service or environment
    - Looking up recent crash reports that match an incident symptom

    Args:
        organization_slug: Sentry organization slug
        sentry_token: Sentry auth token with event read access
        query: Sentry issue search query
        sentry_url: Sentry base URL
        project_slug: Optional Sentry project slug
        limit: Maximum number of issues to return

    Returns:
        issues: Matching Sentry issues
    """
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return {
            "source": "sentry",
            "available": False,
            "error": "Sentry integration is not configured.",
            "issues": [],
        }

    issues = list_sentry_issues(config=config, query=query, limit=limit)
    return {
        "source": "sentry",
        "available": True,
        "issues": issues,
        "query": query,
    }


def get_sentry_issue_details(
    organization_slug: str,
    sentry_token: str,
    issue_id: str,
    sentry_url: str = "",
    project_slug: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch full details for a Sentry issue.

    Useful for:
    - Inspecting the main error group linked to an alert
    - Reviewing culprit, level, and regression details
    - Understanding whether an incident matches an existing issue

    Args:
        organization_slug: Sentry organization slug
        sentry_token: Sentry auth token with event read access
        issue_id: Sentry issue ID
        sentry_url: Sentry base URL
        project_slug: Optional Sentry project slug

    Returns:
        issue: Sentry issue details
    """
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return {
            "source": "sentry",
            "available": False,
            "error": "Sentry integration is not configured.",
            "issue": {},
        }

    issue = get_sentry_issue(config=config, issue_id=issue_id)
    return {
        "source": "sentry",
        "available": True,
        "issue": issue,
    }


def list_sentry_issue_events(
    organization_slug: str,
    sentry_token: str,
    issue_id: str,
    sentry_url: str = "",
    project_slug: str = "",
    limit: int = 10,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent events for a Sentry issue.

    Useful for:
    - Reviewing the latest stack traces attached to an issue
    - Checking whether new events appeared during an incident window
    - Comparing repeated failures grouped under the same issue

    Args:
        organization_slug: Sentry organization slug
        sentry_token: Sentry auth token with event read access
        issue_id: Sentry issue ID
        sentry_url: Sentry base URL
        project_slug: Optional Sentry project slug
        limit: Maximum number of issue events to return

    Returns:
        events: Recent Sentry events for the issue
    """
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return {
            "source": "sentry",
            "available": False,
            "error": "Sentry integration is not configured.",
            "events": [],
        }

    events = sentry_list_issue_events(config=config, issue_id=issue_id, limit=limit)
    return {
        "source": "sentry",
        "available": True,
        "events": events,
    }
