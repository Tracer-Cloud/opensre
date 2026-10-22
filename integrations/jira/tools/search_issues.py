"""Jira issue search tool for investigation workflows."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from integrations.jira.client import make_jira_client


def _map_jira_search_issues(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    issues = output.get("issues", [])
    if isinstance(issues, list) and issues:
        record_evidence_entry(
            evidence,
            source="jira_search_issues",
            label="Jira Search Results",
            summary=f"{len(issues)} issues"
            + (" (more available)" if output.get("truncated") else ""),
        )


class JiraSearchIssuesTool(BaseTool):
    """Search Jira issues via JQL to find related incidents, bugs, or tasks."""

    name = "jira_search_issues"
    source = "jira"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_jira_search_issues
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
    injected_params = ["api_token", "base_url", "email"]
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
        "total": "Total number of matching issues, or null when more pages exist",
        "truncated": "true when more matching issues exist beyond the returned page",
        "next_page_token": "Token for the next page of results; empty on the last page",
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
            return tool_unavailable(
                "jira", "Jira integration is not configured.", issues=[], total=0
            )

        result = client.search_issues(jql=jql, max_results=max_results)

        if not result.get("success"):
            return tool_unavailable(
                "jira", result.get("error", "unknown error"), issues=[], total=0
            )

        return {
            "source": "jira",
            "available": True,
            "issues": result.get("issues", []),
            "total": result.get("total"),
            "truncated": bool(result.get("truncated")),
            "next_page_token": result.get("next_page_token", ""),
            "jql": jql,
        }


jira_search_issues = JiraSearchIssuesTool()
