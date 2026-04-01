"""Sentry issue and event investigation tools."""

from __future__ import annotations

from typing import Any

from app.integrations.sentry import get_sentry_issue
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.SentrySearchIssuesTool import (
    _resolve_config,
    _sentry_available,
    _sentry_creds,
)


class SentryIssueDetailsTool(BaseTool):
    """Fetch full details for a Sentry issue."""

    name = "get_sentry_issue_details"
    source = "sentry"
    description = "Fetch full details for a Sentry issue."
    use_cases = [
        "Inspecting the main error group linked to an alert",
        "Reviewing culprit, level, and regression details",
        "Understanding whether an incident matches an existing issue",
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
        }

    def run(
        self,
        organization_slug: str,
        sentry_token: str,
        issue_id: str,
        sentry_url: str = "",
        project_slug: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        config = _resolve_config(sentry_url, organization_slug, sentry_token, project_slug)
        if config is None:
            return {"source": "sentry", "available": False, "error": "Sentry integration is not configured.", "issue": {}}

        issue = get_sentry_issue(config=config, issue_id=issue_id)
        return {"source": "sentry", "available": True, "issue": issue}


get_sentry_issue_details = SentryIssueDetailsTool()
