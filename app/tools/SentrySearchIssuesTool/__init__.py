"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.sentry import (
    SentryConfig,
    build_sentry_config,
    list_sentry_issues,
    sentry_config_from_env,
)
from app.tools.tool_decorator import tool


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


def _sentry_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("sentry", {}).get("connection_verified"))


def _sentry_creds(sentry: dict[str, Any]) -> dict[str, Any]:
    return {
        "organization_slug": sentry["organization_slug"],
        "sentry_token": sentry["sentry_token"],
        "sentry_url": sentry.get("sentry_url", "https://sentry.io"),
        "project_slug": sentry.get("project_slug", ""),
    }


def _search_issues_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources["sentry"]
    return {
        **_sentry_creds(sentry),
        "query": sentry.get("query", ""),
        "limit": 10,
    }


@tool(
    name="search_sentry_issues",
    source="sentry",
    description="Search Sentry issues related to an incident or failure signature.",
    use_cases=[
        "Checking whether an alert maps to a known Sentry issue",
        "Finding unresolved error groups for a service or environment",
        "Looking up recent crash reports that match an incident symptom",
    ],
    requires=["organization_slug", "sentry_token"],
    input_schema={
        "type": "object",
        "properties": {
            "organization_slug": {"type": "string"},
            "sentry_token": {"type": "string"},
            "query": {"type": "string", "default": ""},
            "sentry_url": {"type": "string", "default": ""},
            "project_slug": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["organization_slug", "sentry_token"],
    },
    is_available=_sentry_available,
    extract_params=_search_issues_extract_params,
    surfaces=("investigation", "chat"),
)
def search_sentry_issues(
    organization_slug: str,
    sentry_token: str,
    query: str = "",
    sentry_url: str = "",
    project_slug: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Search Sentry issues related to an incident or failure signature."""
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return {"source": "sentry", "available": False, "error": "Sentry integration is not configured.", "issues": []}

    issues = list_sentry_issues(config=config, query=query, limit=limit)
    return {"source": "sentry", "available": True, "issues": issues, "query": query}
