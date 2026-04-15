"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.sentry import get_sentry_issue
from app.tools.SentrySearchIssuesTool import (
    _resolve_config,
    _sentry_available,
    _sentry_creds,
)
from app.tools.tool_decorator import tool


def _issue_details_available(sources: dict[str, dict]) -> bool:
    return bool(_sentry_available(sources) and sources.get("sentry", {}).get("issue_id"))


def _issue_details_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources["sentry"]
    return {
        **_sentry_creds(sentry),
        "issue_id": sentry["issue_id"],
    }


@tool(
    name="get_sentry_issue_details",
    source="sentry",
    description="Fetch full details for a Sentry issue.",
    use_cases=[
        "Inspecting the main error group linked to an alert",
        "Reviewing culprit, level, and regression details",
        "Understanding whether an incident matches an existing issue",
    ],
    requires=["organization_slug", "sentry_token", "issue_id"],
    input_schema={
        "type": "object",
        "properties": {
            "organization_slug": {"type": "string"},
            "sentry_token": {"type": "string"},
            "issue_id": {"type": "string"},
            "sentry_url": {"type": "string", "default": ""},
            "project_slug": {"type": "string", "default": ""},
        },
        "required": ["organization_slug", "sentry_token", "issue_id"],
    },
    is_available=_issue_details_available,
    extract_params=_issue_details_extract_params,
    surfaces=("investigation", "chat"),
)
def get_sentry_issue_details(
    organization_slug: str,
    sentry_token: str,
    issue_id: str,
    sentry_url: str = "",
    project_slug: str = "",
) -> dict[str, Any]:
    """Fetch full details for a Sentry issue."""
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return {"source": "sentry", "available": False, "error": "Sentry integration is not configured.", "issue": {}}

    issue = get_sentry_issue(config=config, issue_id=issue_id)
    return {"source": "sentry", "available": True, "issue": issue}
