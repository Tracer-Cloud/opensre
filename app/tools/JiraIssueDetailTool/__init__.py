"""Jira issue detail tool for investigation workflows."""

from __future__ import annotations

from typing import Any

from app.services.jira import make_jira_client
from app.tools.base import BaseTool


class JiraIssueDetailTool(BaseTool):
    """Fetch full details for a specific Jira issue by key."""

    name = "jira_issue_detail"
    source = "jira"
    description = (
        "Fetch the full details of a specific Jira issue to pull context, status, "
        "and description into the current investigation."
    )
    use_cases = [
        "Getting the full description and context of a Jira incident ticket",
        "Checking the current status and priority of a known issue",
        "Reading issue details to correlate with alert findings",
        "Pulling assignee and label information for an existing ticket",
    ]
    requires = ["base_url", "email", "api_token", "issue_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Jira instance URL (e.g. https://myorg.atlassian.net)",
            },
            "email": {"type": "string", "description": "Jira account email for authentication"},
            "api_token": {"type": "string", "description": "Jira API token"},
            "issue_key": {
                "type": "string",
                "description": "Jira issue key to fetch (e.g. OPS-123)",
            },
        },
        "required": ["base_url", "email", "api_token", "issue_key"],
    }
    outputs = {
        "issue": "Full issue details including summary, status, priority, labels, and description",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("jira", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        jira = sources["jira"]
        return {
            "base_url": jira.get("base_url", ""),
            "email": jira.get("email", ""),
            "api_token": jira.get("api_token", ""),
            "issue_key": "",
        }

    def run(
        self,
        base_url: str,
        email: str,
        api_token: str,
        issue_key: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not issue_key:
            return {
                "source": "jira",
                "available": False,
                "error": "issue_key is required. Run jira_search_issues first to find an issue key.",
                "issue": {},
            }

        client = make_jira_client(base_url, email, api_token)
        if client is None:
            return {
                "source": "jira",
                "available": False,
                "error": "Jira integration is not configured.",
                "issue": {},
            }

        result = client.get_issue(issue_key)

        if not result.get("success"):
            return {
                "source": "jira",
                "available": False,
                "error": result.get("error", "unknown error"),
                "issue": {},
            }

        return {
            "source": "jira",
            "available": True,
            "issue_key": issue_key,
            "issue": {
                "issue_key": result.get("issue_key", ""),
                "summary": result.get("summary", ""),
                "status": result.get("status", ""),
                "priority": result.get("priority", ""),
                "labels": result.get("labels", []),
                "description": result.get("description", ""),
            },
        }


jira_issue_detail = JiraIssueDetailTool()
