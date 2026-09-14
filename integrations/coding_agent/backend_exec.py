"""Shared run path for CLI coding-agent backends (workspace checks → run → capture).

Every CLI backend does the same thing around its vendor-specific argv/env: verify
the workspace is an editable git checkout, run the agent process to a deadline,
capture what changed via git, and classify the outcome into a neutral
:class:`CodingResult`. Backends supply only the argv/env via
:func:`run_agentic_cli` so that behavior stays identical across agents.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from integrations.coding_agent.models import CodingResult
from integrations.git import GitCommandError, capture_worktree_changes, is_git_repo
from integrations.llm_cli.agent_exec import classify_agent_outcome, poll_agent_process

_MAX_DIFF_CHARS = 20000


def failure(error: str) -> CodingResult:
    """A pre-flight or execution failure as a neutral result (never raises)."""
    return CodingResult(success=False, summary="", returncode=-1, error=error)


def resolve_workspace_dir(workspace: str) -> str:
    """Expand the workspace path, defaulting to the current directory."""
    return str(Path(workspace).expanduser()) if workspace else os.getcwd()


def workspace_error(ws: str) -> str | None:
    """Why *ws* cannot be edited (not a directory / not a git checkout), or None.

    The backend contract is "edit + return a reviewable diff". Without git we can
    neither capture nor review changes, so callers fail fast *before* editing
    rather than reporting a misleading success with an empty diff.
    """
    if not Path(ws).is_dir():
        return f"workspace is not a directory: {ws}"
    try:
        repo_ok = is_git_repo(ws)
    except GitCommandError as exc:
        return exc.message
    if not repo_ok:
        return f"workspace is not a git repository; the tool needs git to capture changes: {ws}"
    return None


def run_agentic_cli(
    argv: list[str],
    *,
    workspace: str,
    env: dict[str, str],
    timeout_sec: float,
    agent_name: str,
    stdin: str | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
) -> CodingResult:
    """Run a prepared agent CLI invocation in *workspace* and classify the result."""
    outcome = poll_agent_process(
        argv,
        cwd=workspace,
        env=env,
        timeout_sec=timeout_sec,
        stdin=stdin,
        on_stdout_line=on_stdout_line,
    )
    if outcome.spawn_error:
        return failure(outcome.spawn_error)

    changes = capture_worktree_changes(workspace, max_diff_chars=_MAX_DIFF_CHARS)
    classified = classify_agent_outcome(
        outcome,
        agent_name=agent_name,
        changed_files=changes.changed_files,
        timeout_sec=timeout_sec,
    )
    return CodingResult(
        success=classified.success,
        summary=classified.summary,
        changed_files=changes.changed_files,
        diff=changes.diff,
        diff_truncated=changes.diff_truncated,
        returncode=outcome.returncode,
        timed_out=outcome.timed_out,
        error=classified.error,
    )


__all__ = ["failure", "resolve_workspace_dir", "run_agentic_cli", "workspace_error"]
