# ======== from tools/jira_add_comment_tool/ ========

"""Jira comment tool for investigation workflows."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from integrations.jira.client import make_jira_client
from integrations.jira.tools.search_issues import JiraSearchIssuesTool, jira_search_issues


def _map_jira_issue_detail(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    issue = output.get("issue")
    if isinstance(issue, dict) and issue:
        record_evidence_entry(
            evidence,
            source="jira_issue_detail",
            label="Jira Issue Detail",
            summary=str(issue.get("summary") or issue.get("issue_key") or "Issue details"),
        )


class JiraAddCommentTool(BaseTool):
    """Add investigation findings as a comment on an existing Jira issue."""

    name = "jira_add_comment"
    source = "jira"
    description = (
        "Post investigation findings, root cause analysis, or status updates as a comment "
        "on an existing Jira issue to keep the ticket up to date."
    )
    use_cases = [
        "Appending root cause analysis findings to an existing incident ticket",
        "Posting investigation status updates on a Jira issue",
        "Adding evidence or log excerpts as a comment for the incident responders",
        "Documenting resolution steps on the tracking ticket",
    ]
    requires = ["base_url", "email", "api_token", "issue_key", "body"]
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
            "issue_key": {
                "type": "string",
                "description": "Jira issue key to comment on (e.g. OPS-123)",
            },
            "body": {
                "type": "string",
                "description": "Comment text with investigation findings",
            },
        },
        "required": ["base_url", "email", "api_token", "issue_key", "body"],
    }
    outputs = {
        "comment_id": "The ID of the created comment",
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
            "body": "",
        }

    def run(
        self,
        base_url: str,
        email: str,
        api_token: str,
        issue_key: str,
        body: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not issue_key:
            return tool_unavailable("jira", "issue_key is required.", comment_id="")

        if not body:
            return tool_unavailable("jira", "body is required.", comment_id="")

        client = make_jira_client(base_url, email, api_token)
        if client is None:
            return tool_unavailable("jira", "Jira integration is not configured.", comment_id="")

        result = client.add_comment(issue_key=issue_key, body=body)

        if not result.get("success"):
            return tool_unavailable("jira", result.get("error", "unknown error"), comment_id="")

        return {
            "source": "jira",
            "available": True,
            "issue_key": issue_key,
            "comment_id": result.get("comment_id", ""),
        }


jira_add_comment = JiraAddCommentTool()


# ======== from tools/jira_create_issue_tool/ ========

"""Jira issue creation tool for investigation workflows."""


from core.tool import BaseTool


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
            return tool_unavailable(
                "jira", "Jira integration is not configured.", issue_key="", url=""
            )

        result = client.create_issue(
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority,
            labels=labels,
        )

        if not result.get("success"):
            return tool_unavailable(
                "jira", result.get("error", "unknown error"), issue_key="", url=""
            )

        return {
            "source": "jira",
            "available": True,
            "issue_key": result.get("issue_key", ""),
            "issue_id": result.get("issue_id", ""),
            "url": result.get("url", ""),
        }


jira_create_issue = JiraCreateIssueTool()


# ======== from tools/jira_issue_detail_tool/ ========

"""Jira issue detail tool for investigation workflows."""


from core.tool import BaseTool


class JiraIssueDetailTool(BaseTool):
    """Fetch full details for a specific Jira issue by key."""

    name = "jira_issue_detail"
    source = "jira"
    evidence_mapper = _map_jira_issue_detail
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
            return tool_unavailable(
                "jira",
                "issue_key is required. Run jira_search_issues first to find an issue key.",
                issue={},
            )

        client = make_jira_client(base_url, email, api_token)
        if client is None:
            return tool_unavailable("jira", "Jira integration is not configured.", issue={})

        result = client.get_issue(issue_key)

        if not result.get("success"):
            return tool_unavailable("jira", result.get("error", "unknown error"), issue={})

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


__all__ = [
    "JiraAddCommentTool",
    "JiraCreateIssueTool",
    "JiraIssueDetailTool",
    "JiraSearchIssuesTool",
    "jira_add_comment",
    "jira_create_issue",
    "jira_issue_detail",
    "jira_search_issues",
]
