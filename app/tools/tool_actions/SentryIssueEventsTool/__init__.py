"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.sentry import list_sentry_issue_events as sentry_list_issue_events
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.SentrySearchIssuesTool import (
    _resolve_config,
    _sentry_available,
    _sentry_creds,
)


class SentryIssueEventsTool(BaseTool):
    """List recent events for a Sentry issue."""

    name = "list_sentry_issue_events"
    source = "sentry"
    description = "List recent events for a Sentry issue."
    use_cases = [
        "Reviewing the latest stack traces attached to an issue",
        "Checking whether new events appeared during an incident window",
        "Comparing repeated failures grouped under the same issue",
    ]
    requires = ["organization_slug", "sentry_token", "issue_id"]
    input_schema = {
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
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_sentry_available(sources) and sources.get("sentry", {}).get("issue_id"))

    def extract_params(self, sources: dict) -> dict:
        sentry = sources["sentry"]
        return {
            **_sentry_creds(sentry),
            "issue_id": sentry["issue_id"],
            "limit": 10,
        }

    def run(
        self,
        organization_slug: str,
        sentry_token: str,
        issue_id: str,
        sentry_url: str = "",
        project_slug: str = "",
        limit: int = 10,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
        if config is None:
            return {"source": "sentry", "available": False, "error": "Sentry integration is not configured.", "events": []}

        events = sentry_list_issue_events(config=config, issue_id=issue_id, limit=limit)
        return {"source": "sentry", "available": True, "events": events}


list_sentry_issue_events = SentryIssueEventsTool()
