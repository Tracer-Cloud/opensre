"""Jira issue creation tool for investigation workflows."""

from __future__ import annotations

from typing import Any

from app.services.jira import make_jira_client
from app.tools.base import BaseTool


class JiraCreateIssueTool(BaseTool):
    """Create a Jira issue to track an incident discovered during investigation."""

    name = "jira_create_issue"
    source = "jira"
    description = (
        "Create a new Jira issue to file an incident ticket with investigation findings, "
        "including summary, description, priority, and labels."
    )
    use_cases = [
        "Filing a new incident ticket after root cause analysis",
        "Creating a bug report from investigation findings",
        "Tracking a production issue discovered during alert investigation",
        "Documenting a new issue with evidence from the investigation",
    ]
    requires = ["base_url", "email", "api_token", "summary", "description"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Jira instance URL (e.g. https://myorg.atlassian.net)",
            },
            "email": {"type": "string", "description": "Jira account email for authentication"},
            "api_token": {"type": "string", "description": "Jira API token"},
            "project_key": {
                "type": "string",
                "default": "",
                "description": "Jira project key (e.g. OPS). Uses configured default if empty.",
            },
            "summary": {"type": "string", "description": "Issue title/summary"},
            "description": {
                "type": "string",
                "description": "Issue description with investigation findings",
            },
            "issue_type": {
                "type": "string",
                "default": "Bug",
                "description": "Jira issue type (e.g. Bug, Task, Incident)",
            },
            "priority": {
                "type": "string",
                "default": "High",
                "description": "Issue priority (e.g. Highest, High, Medium, Low, Lowest)",
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "Labels to attach to the issue",
            },
        },
        "required": ["base_url", "email", "api_token", "summary", "description"],
    }
    outputs = {
        "issue_key": "The key of the created issue (e.g. OPS-456)",
        "url": "Direct URL to the created issue",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("jira", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        jira = sources["jira"]
        return {
            "base_url": jira.get("base_url", ""),
            "email": jira.get("email", ""),
            "api_token": jira.get("api_token", ""),
            "project_key": jira.get("project_key", ""),
            "summary": "",
            "description": "",
            "issue_type": "Bug",
            "priority": "High",
            "labels": [],
        }

    def run(
        self,
        base_url: str,
        email: str,
        api_token: str,
        summary: str,
        description: str,
        project_key: str = "",
        issue_type: str = "Bug",
        priority: str = "High",
        labels: list[str] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_jira_client(base_url, email, api_token, project_key)
        if client is None:
            return {
                "source": "jira",
                "available": False,
                "error": "Jira integration is not configured.",
                "issue_key": "",
                "url": "",
            }

        result = client.create_issue(
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority,
            labels=labels,
        )

        if not result.get("success"):
            return {
                "source": "jira",
                "available": False,
                "error": result.get("error", "unknown error"),
                "issue_key": "",
                "url": "",
            }

        return {
            "source": "jira",
            "available": True,
            "issue_key": result.get("issue_key", ""),
            "issue_id": result.get("issue_id", ""),
            "url": result.get("url", ""),
        }


jira_create_issue = JiraCreateIssueTool()
