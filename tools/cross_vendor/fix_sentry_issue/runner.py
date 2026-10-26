"""Lifecycle/orchestration for the Sentry issue-fix tool.

Thin free functions the tool's ``run`` drives: opt-in gates (fix + ship), coding-agent
readiness, the coding run (via the agent-neutral ``integrations/coding_agent`` seam),
the optional ship step (delegated to ``ship.py``), and result shaping.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Final

from integrations.coding_agent import (
    CodingResult,
    coding_model,
    coding_timeout_seconds,
    coding_workspace,
    run_coding_task,
    verify_coding_agent,
)
from integrations.git import (
    GitCommandError,
    changed_paths,
    ensure_git_repo,
    file_fingerprints,
    is_git_repo,
)
from integrations.github import github_rest_token, github_setup_url
from tools.cross_vendor.fix_sentry_issue.context import IssueContext
from tools.cross_vendor.fix_sentry_issue.errors import (
    ERR_CLI_UNAVAILABLE,
    ERR_DISABLED,
    ERR_EXECUTION,
    ERR_GITHUB_TOKEN,
    ERR_SHIP_DISABLED,
    ERR_TIMEOUT,
    FixIssueError,
)
from tools.cross_vendor.fix_sentry_issue.ship import ShipResult, ship_fix

SOURCE: Final = "sentry"
_TRUTHY = {"1", "true", "yes", "on"}


def is_issue_fix_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the Sentry issue-fix tool is opted in via ``PI_ISSUE_FIX_ENABLED``."""
    source = env if env is not None else os.environ
    return source.get("PI_ISSUE_FIX_ENABLED", "").strip().lower() in _TRUTHY


def ensure_enabled() -> None:
    if not is_issue_fix_enabled():
        raise FixIssueError(
            ERR_DISABLED,
            "Sentry issue-fix tool is disabled. Set PI_ISSUE_FIX_ENABLED=1 "
            "(plus Sentry config and a coding agent) to enable it.",
        )


def ensure_cli_ready() -> None:
    available, detail = verify_coding_agent()
    if not available:
        raise FixIssueError(ERR_CLI_UNAVAILABLE, f"Coding agent is not ready: {detail}")


def resolve_workspace(workspace: str | None) -> str:
    """Resolve the workspace once so the fix and the ship step operate on the same tree."""
    return workspace or coding_workspace()


def run_fix(ctx: IssueContext, workspace: str, model: str | None) -> CodingResult:
    return run_coding_task(
        ctx.task,
        workspace=workspace,
        model=model or coding_model(),
        timeout_sec=coding_timeout_seconds(),
    )


def is_ship_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether opening a PR is opted in via ``PI_ISSUE_FIX_SHIP_ENABLED``."""
    source = env if env is not None else os.environ
    return source.get("PI_ISSUE_FIX_SHIP_ENABLED", "").strip().lower() in _TRUTHY


def extract_github_params(sources: dict[str, dict]) -> dict[str, Any]:
    github = sources.get("github", {})
    return {
        "github_token": github_rest_token(sources),
        "github_connection_origin": github.get("connection_origin", ""),
    }


def eligible_github_token(token: str, origin: str | None) -> str:
    return github_rest_token(explicit=token, connection_origin=origin)


def ensure_ship_ready(workspace: str, github_token: str) -> None:
    """Fail fast (before the Pi run) if a requested PR can't possibly be opened."""
    if not is_ship_enabled():
        raise FixIssueError(
            ERR_SHIP_DISABLED,
            "Opening a PR is disabled. Set PI_ISSUE_FIX_SHIP_ENABLED=1 (and an authorized GitHub app connection) "
            "to let OpenSRE ship the fix as a pull request.",
        )
    if not github_token:
        raise FixIssueError(
            ERR_GITHUB_TOKEN,
            f"Connect GitHub in the OpenSRE app before opening a PR: {github_setup_url()}",
        )
    try:
        ensure_git_repo(workspace)
    except GitCommandError as exc:
        raise FixIssueError(exc.kind, exc.message) from exc


def pre_pi_changes(workspace: str) -> dict[str, str]:
    """Content fingerprint of files already dirty *before* the Pi run (path -> hash).

    Captured so the ship step can commit only what Pi actually creates or modifies —
    excluding pre-existing developer work-in-progress it leaves untouched, while still
    including a file Pi edits that happened to be dirty already. Empty (best-effort)
    when the workspace is clean, not a repo, or git is unavailable.
    """
    try:
        if not is_git_repo(workspace):
            return {}
        return file_fingerprints(workspace, changed_paths(workspace))
    except GitCommandError:
        return {}


def run_ship(
    issue_id: str,
    sentry_url: str,
    result: CodingResult,
    workspace: str,
    baseline: Mapping[str, str] | None = None,
    github_token: str = "",
) -> ShipResult:
    return ship_fix(
        workspace,
        issue_id=issue_id,
        sentry_url=sentry_url,
        result=result,
        baseline=baseline,
        github_token=github_token,
    )


def _base_output(issue_id: str) -> dict[str, Any]:
    """Stable output shape shared by every return path, so all keys are always present.

    Callers can read ``pr_url``/``branch_name``/``diff`` etc. without guarding for
    early-gate failures that never reached the Pi run or the ship step.
    """
    return {
        "source": SOURCE,
        "success": False,
        "error_kind": None,
        "issue_id": issue_id,
        "summary": "",
        "changed_files": [],
        "diff": "",
        "diff_truncated": False,
        "error": None,
        "branch_name": None,
        "pr_url": None,
        "pr_number": None,
    }


def to_output(issue_id: str, result: CodingResult) -> dict[str, Any]:
    error_kind: str | None = None
    if not result.success:
        error_kind = ERR_TIMEOUT if result.timed_out else ERR_EXECUTION
    return {
        **_base_output(issue_id),
        "success": result.success,
        "error_kind": error_kind,
        "summary": result.summary,
        "changed_files": result.changed_files,
        "diff": result.diff,
        "diff_truncated": result.diff_truncated,
        "error": result.error,
    }


def with_ship_output(output: dict[str, Any], ship: ShipResult) -> dict[str, Any]:
    """Merge a successful ship result (branch + PR) into the tool output."""
    return {
        **output,
        "branch_name": ship.branch_name,
        "pr_url": ship.pr.url,
        "pr_number": ship.pr.number,
    }


def ship_error_output(output: dict[str, Any], exc: FixIssueError) -> dict[str, Any]:
    """The fix succeeded but shipping failed: surface the ship error, keep the diff.

    When the failure happened after the fix was committed to a branch, carry that
    ``branch_name`` so the caller can guide manual recovery (push / open PR by hand).
    """
    return {
        **output,
        "success": False,
        "error_kind": exc.kind,
        "error": exc.message,
        "branch_name": exc.branch_name,
    }


def error_output(kind: str, message: str, issue_id: str = "") -> dict[str, Any]:
    return {
        **_base_output(issue_id),
        "error_kind": kind,
        "error": message,
    }
