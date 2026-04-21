"""Jira comment tool for investigation workflows."""

from __future__ import annotations

from typing import Any

from app.services.jira import make_jira_client
from app.tools.base import BaseTool


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
            return {
                "source": "jira",
                "available": False,
                "error": "issue_key is required.",
                "comment_id": "",
            }

        if not body:
            return {
                "source": "jira",
                "available": False,
                "error": "body is required.",
                "comment_id": "",
            }

        client = make_jira_client(base_url, email, api_token)
        if client is None:
            return {
                "source": "jira",
                "available": False,
                "error": "Jira integration is not configured.",
                "comment_id": "",
            }

        result = client.add_comment(issue_key=issue_key, body=body)

        if not result.get("success"):
            return {
                "source": "jira",
                "available": False,
                "error": result.get("error", "unknown error"),
                "comment_id": "",
            }

        return {
            "source": "jira",
            "available": True,
            "issue_key": issue_key,
            "comment_id": result.get("comment_id", ""),
        }


jira_add_comment = JiraAddCommentTool()
