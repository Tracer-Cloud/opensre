"""Action tool: scan the local machine for git repositories and show their activity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.agent_harness.tools import action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from tools.system.workspace_git_scan.render import render_snapshot, snapshot_text
from tools.system.workspace_git_scan.scan import WorkspaceSnapshot, scan_workspace

_DEFAULT_DAYS = 30
_MAX_DAYS = 365

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root": {
            "type": "string",
            "description": "Directory to scan. Defaults to the user's home directory.",
        },
        "days": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_DAYS,
            "description": f"Commit window in days, default {_DEFAULT_DAYS}.",
        },
    },
    "additionalProperties": False,
}


def _console(context: Any) -> Any:
    if context is None:
        return None
    try:
        return action_context_from_agent_context(context).console
    except RuntimeError:
        return None


def _repo_payload(snapshot: WorkspaceSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "name": repo.name,
            "path": repo.path,
            "github": repo.github_full_name,
            "commits": repo.commits,
            "own_commits": repo.own_commits,
            "uncommitted": repo.uncommitted,
            "has_workflows": repo.has_workflows,
        }
        for repo in snapshot.repos
    ]


@tool(
    name="scan_local_git_workspace",
    source="system",
    display_name="Scan local repositories",
    description=(
        "Find git repositories on this machine, count their commits in a recent "
        "window and their uncommitted files, note which have GitHub Actions "
        "workflows, and draw the activity bar chart in the shell. Read-only."
    ),
    use_cases=[
        "Show which repositories on this machine are active and how many commits they had",
        "Find local checkouts that have GitHub Actions workflows configured",
        "Pick a real repository for a demo from the user's own machine",
    ],
    anti_examples=[
        "Reading GitHub Actions run history (use the GitHub CI tools)",
        "Listing repositories on GitHub that are not checked out locally (use github_cli)",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    parallel_safe=False,
    accepts_runtime_context=True,
    input_schema=_INPUT_SCHEMA,
    tags=("safe", "no-credentials"),
)
def scan_local_git_workspace(
    root: str | None = None,
    days: int | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Scan for local git checkouts and render the activity snapshot."""
    window = min(max(int(days or _DEFAULT_DAYS), 1), _MAX_DAYS)
    scan_root = Path(root).expanduser() if root else Path.home()
    if not scan_root.is_dir():
        return {
            "source": "system",
            "success": False,
            "error": f"{scan_root} is not a directory.",
            "response_text": f"{scan_root} is not a directory; nothing was scanned.",
        }
    snapshot = scan_workspace(scan_root, days=window)
    console = _console(context)
    rendered = console is not None
    if rendered:
        render_snapshot(console, snapshot)
    with_workflows = sum(1 for repo in snapshot.repos if repo.has_workflows)
    summary = (
        f"Found {len(snapshot.repos)} git repositories under {snapshot.root}: "
        f"{snapshot.total_commits} commits in the last {window} days "
        f"({snapshot.total_own_commits} by you), "
        f"{snapshot.total_uncommitted} uncommitted files, "
        f"{with_workflows} with GitHub Actions workflows."
    )
    return {
        "source": "system",
        "success": True,
        "root": snapshot.root,
        "days": window,
        "repo_count": len(snapshot.repos),
        "total_commits": snapshot.total_commits,
        "total_own_commits": snapshot.total_own_commits,
        "total_uncommitted": snapshot.total_uncommitted,
        "repos_with_workflows": with_workflows,
        "truncated": snapshot.truncated,
        "repos": _repo_payload(snapshot),
        "rendered_in_shell": rendered,
        "summary": summary,
        "response_text": summary if rendered else f"{snapshot_text(snapshot)}\n\n{summary}",
    }


__all__ = ["scan_local_git_workspace"]
