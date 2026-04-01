"""Sentry investigation tools."""

from app.tools.tool_actions.base import BaseTool
from app.tools.tool_actions.sentry.sentry_actions import (
    SentryIssueDetailsTool,
    SentryIssueEventsTool,
    SentrySearchIssuesTool,
    get_sentry_issue_details,
    list_sentry_issue_events,
    search_sentry_issues,
)

TOOLS: list[BaseTool] = [
    SentrySearchIssuesTool(),
    SentryIssueDetailsTool(),
    SentryIssueEventsTool(),
]

__all__ = [
    "TOOLS",
    "SentryIssueDetailsTool",
    "SentryIssueEventsTool",
    "SentrySearchIssuesTool",
    "get_sentry_issue_details",
    "list_sentry_issue_events",
    "search_sentry_issues",
]
