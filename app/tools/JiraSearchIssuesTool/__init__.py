"""Jira issue search tool for investigation workflows."""

from __future__ import annotations

from typing import Any

from app.services.jira import make_jira_client
from app.tools.base import BaseTool


class JiraSearchIssuesTool(BaseTool):
    """Search Jira issues via JQL to find related incidents, bugs, or tasks."""

    name = "jira_search_issues"
    source = "jira"
    description = (
        "Search Jira issues using JQL to find related incidents, open bugs, or recent tasks "
        "that may provide context for the current investigation."
    )
    use_cases = [
        "Finding open bugs or incidents for a specific service or component",
        "Searching for recent Jira issues related to the alert under investigation",
        "Checking whether a similar incident was already filed in Jira",
        "Listing high-priority issues updated recently in a project",
    ]
    requires = ["base_url", "email", "api_token"]
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
                "description": "Jira project key to scope the search (e.g. OPS)",
            },
            "jql": {
                "type": "string",
                "default": "",
                "description": "JQL query string (e.g. status = Open AND priority = High)",
            },
            "max_results": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of issues to return",
            },
        },
        "required": ["base_url", "email", "api_token"],
    }
    outputs = {
        "issues": "List of issues with key, summary, status, priority, labels, and assignee",
        "total": "Total number of matching issues",
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
            "jql": "",
            "max_results": 20,
        }

    def run(
        self,
        base_url: str,
        email: str,
        api_token: str,
        project_key: str = "",
        jql: str = "",
        max_results: int = 20,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_jira_client(base_url, email, api_token, project_key)
        if client is None:
            return {
                "source": "jira",
                "available": False,
                "error": "Jira integration is not configured.",
                "issues": [],
                "total": 0,
            }

        result = client.search_issues(jql=jql, max_results=max_results)

        if not result.get("success"):
            return {
                "source": "jira",
                "available": False,
                "error": result.get("error", "unknown error"),
                "issues": [],
                "total": 0,
            }

        return {
            "source": "jira",
            "available": True,
            "issues": result.get("issues", []),
            "total": result.get("total", 0),
            "jql": jql,
        }


jira_search_issues = JiraSearchIssuesTool()
