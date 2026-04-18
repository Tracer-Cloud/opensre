"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.sentry import list_sentry_issue_events as sentry_list_issue_events
from app.tools.SentrySearchIssuesTool import (
    _resolve_config,
    _sentry_available,
    _sentry_creds,
)
from app.tools.tool_decorator import tool


def _issue_events_available(sources: dict[str, dict]) -> bool:
    return bool(_sentry_available(sources) and sources.get("sentry", {}).get("issue_id"))


def _issue_events_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    sentry = sources["sentry"]
    return {
        **_sentry_creds(sentry),
        "issue_id": sentry["issue_id"],
        "limit": 10,
    }


@tool(
    name="list_sentry_issue_events",
    source="sentry",
    description="List recent events for a Sentry issue.",
    use_cases=[
        "Reviewing the latest stack traces attached to an issue",
        "Checking whether new events appeared during an incident window",
        "Comparing repeated failures grouped under the same issue",
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
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["organization_slug", "sentry_token", "issue_id"],
    },
    is_available=_issue_events_available,
    extract_params=_issue_events_extract_params,
    surfaces=("investigation", "chat"),
)
def list_sentry_issue_events(
    organization_slug: str,
    sentry_token: str,
    issue_id: str,
    sentry_url: str = "",
    project_slug: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """List recent events for a Sentry issue."""
    config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
    if config is None:
        return {"source": "sentry", "available": False, "error": "Sentry integration is not configured.", "events": []}

    events = sentry_list_issue_events(config=config, issue_id=issue_id, limit=limit)
    return {"source": "sentry", "available": True, "events": events}
