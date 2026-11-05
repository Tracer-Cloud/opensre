"""Read-only Gitee code-analysis tools."""

from __future__ import annotations

from integrations.gitee.tools.gitee_code_tool.tool import (
    compare_gitee_refs,
    get_gitee_file,
    list_gitee_repos,
    list_gitee_tree,
    search_gitee_files,
)

TOOL_MODULES = ("tool",)

__all__ = [
    "TOOL_MODULES",
    "compare_gitee_refs",
    "get_gitee_file",
    "list_gitee_repos",
    "list_gitee_tree",
    "search_gitee_files",
]
