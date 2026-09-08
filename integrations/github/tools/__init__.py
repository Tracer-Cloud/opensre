"""GitHub-backed agent tools."""

from __future__ import annotations

TOOL_MODULES = (
    "actions",
    "ci_analytics",
    "ci_fix",
    "commits",
    "file_contents",
    "issues",
    "repository",
    "repository_tree",
    "search_code",
    "security_fix",
    "stargazers",
    "work_status",
)

__all__ = ["TOOL_MODULES"]
