"""Action tool: scan the local machine for git repositories and show their activity."""

from __future__ import annotations

import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from core.agent_harness.tools import action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from tools.system.workspace_git_scan.prefetch import (
    ScanRequest,
    claim_scan_prefetch,
    start_scan_prefetch,
)
from tools.system.workspace_git_scan.render import render_snapshot, snapshot_text
from tools.system.workspace_git_scan.scan import ScanStop, WorkspaceSnapshot, scan_workspace
from tools.system.workspace_git_scan.skips import default_skip_paths

_DEFAULT_DAYS = 30
_MAX_DAYS = 365

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root": {
            "type": "string",
            "description": (
                "Directory to scan. Defaults to the user's home directory, where media "
                "folders and, on macOS, Desktop, Documents and Downloads are skipped; "
                "name one of those here to scan it."
            ),
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


def _cancellation(console: Any) -> Callable[[], bool] | None:
    """Report whether the user pressed ESC, so the scan stops at the next folder or repository."""
    if console is None:
        return None
    return lambda: bool(getattr(console, "cancel_requested", False))


def _progress(context: Any) -> Callable[[str], None] | None:
    """Relay scan progress lines to the shell as tool updates."""
    emit = getattr(context, "emit_update", None)
    if emit is None:
        return None
    return lambda text: emit({"progress": text})


def _working_directory() -> Path | None:
    try:
        return Path.cwd().resolve()
    except OSError:
        return None


def _scan_request(root: str | None, days: int | None) -> ScanRequest:
    """The scan's arguments for a call with ``root`` and ``days``, prefetched or not."""
    window = min(max(int(days or _DEFAULT_DAYS), 1), _MAX_DAYS)
    # Skip paths match the walk's spelling of each folder, so home, the root and
    # the working directory are all compared in their resolved form.
    home = Path.home().resolve()
    return ScanRequest(
        root=Path(root).expanduser().resolve() if root else home,
        days=window,
        skip_paths=default_skip_paths(home, cwd=_working_directory(), platform=sys.platform),
    )


def _scan(
    request: ScanRequest,
    *,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> WorkspaceSnapshot:
    return scan_workspace(
        request.root,
        days=request.days,
        skip_paths=request.skip_paths,
        should_stop=should_stop,
        on_progress=on_progress,
    )


def _prefetch_scan(request: ScanRequest, should_stop: Callable[[], bool]) -> WorkspaceSnapshot:
    return _scan(request, should_stop=should_stop)


def prefetch_workspace_scan(root: str | None = None, days: int | None = None) -> bool:
    """Start the scan a ``scan_local_git_workspace(root, days)`` call would run, in the background.

    That call, made within a couple of minutes, takes the result instead of
    scanning again. Never renders or reports. False when nothing was started.
    """
    request = _scan_request(root, days)
    if not request.root.is_dir():
        return False
    return start_scan_prefetch(request, partial(_prefetch_scan, request))


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
    """Scan for local git checkouts and render the activity snapshot.

    A cancelled scan returns ``cancelled: True`` and renders nothing. A matching
    scan from :func:`prefetch_workspace_scan` is used instead of a new one.
    """
    request = _scan_request(root, days)
    window = request.days
    scan_root = request.root
    if not scan_root.is_dir():
        return {
            "source": "system",
            "success": False,
            "error": f"{scan_root} is not a directory.",
            "response_text": f"{scan_root} is not a directory; nothing was scanned.",
        }
    console = _console(context)
    should_stop = _cancellation(console)
    # A scan started when the menu was answered stands in for this one; a
    # cancel while it finishes reaches the live scan below, which stops at once.
    snapshot = claim_scan_prefetch(request, should_stop=should_stop)
    if snapshot is None:
        snapshot = _scan(request, should_stop=should_stop, on_progress=_progress(context))
    if snapshot.stop_reason is ScanStop.CANCELLED:
        return {
            "source": "system",
            "success": False,
            "cancelled": True,
            "stop_reason": ScanStop.CANCELLED.value,
            "root": snapshot.root,
            "response_text": f"The scan of {snapshot.root} was cancelled; nothing was shown.",
        }
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
        "stop_reason": snapshot.stop_reason.value if snapshot.stop_reason else None,
        "skipped": list(snapshot.skipped),
        "repos": _repo_payload(snapshot),
        "rendered_in_shell": rendered,
        "summary": summary,
        "response_text": summary if rendered else f"{snapshot_text(snapshot)}\n\n{summary}",
    }


__all__ = ["prefetch_workspace_scan", "scan_local_git_workspace"]
