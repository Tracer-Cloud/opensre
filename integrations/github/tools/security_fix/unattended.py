"""Confirmation-free security fix for scheduled ticks, isolated in a linked worktree.

The interactive tool edits the caller's checkout and asks twice. A scheduled
tick has nobody to ask and must not disturb a checkout a developer may be using,
so it works in a fresh linked worktree of the configured checkout, based on the
freshly fetched default branch, and removes that worktree when the tick ends.
Findings that already have an ``opensre/github-security-fix-*`` pull request
open are skipped so a 30-minute cadence never opens the same fix twice.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from infrastructure.observability.errors.sentry import capture_exception
from integrations.coding_agent import verify_coding_agent
from integrations.git import (
    GitCommandError,
    add_detached_worktree,
    create_branch,
    default_branch,
    fetch_remote_branch,
    linked_worktree_path,
    remove_worktree,
)
from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.tools.security_fix.claim import claim_repository
from integrations.github.tools.security_fix.context import (
    SecurityAlertContext,
    gather_security_alert_context,
)
from integrations.github.tools.security_fix.errors import (
    ERR_FIX_IN_PROGRESS,
    ERR_GITHUB_UNAVAILABLE,
    ERR_PR_FAILED,
    GitHubSecurityFixError,
)
from integrations.github.tools.security_fix.runner import (
    ensure_ship_ready,
    ensure_workspace_ready,
    error_output,
    resolve_workspace,
    run_fix,
    run_ship,
    ship_error_output,
    to_output,
    with_ship_output,
)
from integrations.github.tools.security_fix.ship import build_branch_name, parse_fix_branch_name

_WORKTREE_PREFIX = ".opensre-security-fix"
_MAX_OPEN_PR_PAGES = 2
_OPEN_PR_PAGE_SIZE = 100
logger = logging.getLogger(__name__)


def open_fix_alert_keys(
    client: GitHubRestClient, *, owner: str, repo: str
) -> frozenset[tuple[str, int]]:
    """Return ``(alert_type, number)`` for findings whose OpenSRE fix PR is still open."""
    pulls = client.paginate(
        f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/pulls",
        params={"state": "open", "per_page": _OPEN_PR_PAGE_SIZE},
        max_pages=_MAX_OPEN_PR_PAGES,
        require_complete=True,
    )
    keys: set[tuple[str, int]] = set()
    repository = f"{owner}/{repo}".casefold()
    for pull in pulls:
        head = pull.get("head")
        if not isinstance(head, dict):
            continue
        head_repo = head.get("repo")
        if not isinstance(head_repo, dict):
            continue
        if str(head_repo.get("full_name") or "").casefold() != repository:
            continue
        ref = str(head.get("ref") or "")
        parsed = parse_fix_branch_name(ref)
        if parsed is not None:
            keys.add(parsed)
    return frozenset(keys)


def run_unattended_security_fix(
    *,
    owner: str,
    repo: str,
    alert_type: str = "auto",
    workspace: str | None = None,
    github_token: str | None = None,
    client: GitHubRestClient | None = None,
) -> dict[str, Any]:
    """Fix one open finding of ``owner/repo`` and open a PR without asking anyone.

    Returns the same result shape as :func:`~integrations.github.tools.security_fix.runner.run_security_fix`.
    """
    with claim_repository(owner, repo) as acquired:
        if not acquired:
            return error_output(
                ERR_FIX_IN_PROGRESS, "Another security fix is active for this repository."
            )
        return _run_claimed_security_fix(
            owner=owner,
            repo=repo,
            alert_type=alert_type,
            workspace=workspace,
            github_token=github_token,
            client=client,
        )


def _run_claimed_security_fix(
    *,
    owner: str,
    repo: str,
    alert_type: str,
    workspace: str | None,
    github_token: str | None,
    client: GitHubRestClient | None,
) -> dict[str, Any]:
    ws = resolve_workspace(workspace)
    token = resolve_github_token(github_token) or None
    ctx: SecurityAlertContext | None = None
    try:
        ensure_workspace_ready(ws, owner, repo)
        ensure_ship_ready(ws, github_token=token)
        github = client or GitHubRestClient(token)
        in_flight = open_fix_alert_keys(github, owner=owner, repo=repo)
        coding_available, _coding_detail = verify_coding_agent()
        ctx = gather_security_alert_context(
            owner=owner,
            repo=repo,
            alert_type=alert_type,
            workspace=ws,
            github_token=token,
            prefer_builtin_local_fix=not coding_available,
            exclude=in_flight,
        )
    except GitHubApiError as exc:
        logger.exception("Could not list open security-fix pull requests for %s/%s", owner, repo)
        capture_exception(exc)
        return error_output(
            ERR_GITHUB_UNAVAILABLE,
            f"Could not list open pull requests for {owner}/{repo}; check the local logs.",
        )
    except GitHubSecurityFixError as exc:
        return error_output(exc.kind, exc.message, ctx)
    return _fix_in_linked_worktree(ctx, ws, token)


def _fix_in_linked_worktree(
    ctx: SecurityAlertContext, workspace: str, token: str | None
) -> dict[str, Any]:
    try:
        base = default_branch(workspace, token=token)
        if not base:
            raise GitHubSecurityFixError(
                ERR_PR_FAILED,
                f"Could not determine the default branch of {workspace} to base the fix on.",
            )
        fetch_remote_branch(workspace, base)
        path = str(linked_worktree_path(workspace, _WORKTREE_PREFIX))
        add_detached_worktree(workspace, path, f"origin/{base}")
    except GitCommandError as exc:
        return error_output(exc.kind, exc.message, ctx)
    except GitHubSecurityFixError as exc:
        return error_output(exc.kind, exc.message, ctx)

    branch = ""
    try:
        candidate = build_branch_name(path, ctx)
        create_branch(path, candidate, base_default=base)
        branch = candidate
        result = run_fix(ctx, path, None)
        output = to_output(ctx, result)
        if not result.success:
            return output
        try:
            ship = run_ship(ctx, result, path, baseline={}, github_token=token)
        except GitHubSecurityFixError as exc:
            return ship_error_output(output, exc)
        return with_ship_output(output, ship)
    except GitCommandError as exc:
        return error_output(exc.kind, exc.message, ctx)
    finally:
        remove_worktree(workspace, path, branch=branch)


__all__ = ["open_fix_alert_keys", "run_unattended_security_fix"]
