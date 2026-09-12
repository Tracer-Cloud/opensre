"""Lifecycle for fixing GitHub security or quality findings with a coding agent."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
from typing import Any, Final

from integrations.coding_agent import (
    CodingResult,
    coding_model,
    coding_timeout_seconds,
    coding_workspace,
    run_coding_task,
    verify_coding_agent,
)
from integrations.git import GitCommandError, changed_paths, ensure_git_repo, file_fingerprints
from integrations.github.client import resolve_github_token
from integrations.github.repair_workspace import repair_workspace
from integrations.github.tools.security_fix.context import (
    SecurityAlertContext,
    gather_security_alert_context,
)
from integrations.github.tools.security_fix.errors import (
    ERR_CLI_UNAVAILABLE,
    ERR_CONFIRMATION_DENIED,
    ERR_EXECUTION,
    ERR_GITHUB_TOKEN,
    ERR_TIMEOUT,
    GitHubSecurityFixError,
)
from integrations.github.tools.security_fix.local_fix import try_builtin_local_fix
from integrations.github.tools.security_fix.ship import ShipResult, ship_security_fix

SOURCE: Final = "github"
_YES = {"y", "yes"}


def ensure_cli_ready() -> None:
    available, detail = verify_coding_agent()
    if not available:
        raise GitHubSecurityFixError(ERR_CLI_UNAVAILABLE, f"Coding agent is not ready: {detail}")


def ensure_ship_ready(workspace: str, github_token: str | None = None) -> None:
    if not resolve_github_token(github_token):
        raise GitHubSecurityFixError(
            ERR_GITHUB_TOKEN,
            "A GitHub token is required to open a PR. Set GITHUB_TOKEN or GH_TOKEN.",
        )
    try:
        ensure_git_repo(workspace)
    except GitCommandError as exc:
        raise GitHubSecurityFixError(exc.kind, exc.message) from exc


def pre_coding_changes(workspace: str) -> dict[str, str]:
    """Fingerprint already-dirty files so shipping excludes untouched WIP."""
    try:
        return file_fingerprints(workspace, changed_paths(workspace))
    except GitCommandError:
        return {}


def run_fix(ctx: SecurityAlertContext, workspace: str, model: str | None) -> CodingResult:
    local_result, local_reason = try_builtin_local_fix(ctx, workspace)
    if local_result is not None and local_result.success:
        return local_result

    available, _detail = verify_coding_agent()
    if not available:
        reason = local_result.error if local_result is not None else local_reason
        return CodingResult(
            success=False,
            summary="",
            error=_unsupported_without_fallback_message(reason),
            returncode=-1,
        )

    return run_coding_task(
        ctx.task,
        workspace=workspace,
        model=model or coding_model(),
        timeout_sec=coding_timeout_seconds(),
    )


def run_ship(
    ctx: SecurityAlertContext,
    result: CodingResult,
    workspace: str,
    *,
    baseline: Mapping[str, str] | None = None,
    github_token: str | None = None,
) -> ShipResult:
    return ship_security_fix(
        workspace,
        ctx=ctx,
        result=result,
        baseline=baseline,
        github_token=github_token,
    )


def require_confirmation(
    confirm_fn: Callable[[str], str] | None,
    prompt: str,
) -> None:
    """Ask for local confirmation when a REPL confirmation function is present."""
    if confirm_fn is None:
        return
    answer = confirm_fn(prompt)
    if answer.strip().lower() not in _YES:
        raise GitHubSecurityFixError(ERR_CONFIRMATION_DENIED, "User denied the requested action.")


def _base_output(ctx: SecurityAlertContext | None = None) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "success": False,
        "error_kind": None,
        "owner": ctx.owner if ctx else "",
        "repo": ctx.repo if ctx else "",
        "alert_type": ctx.alert_type if ctx else "",
        "alert_number": ctx.number if ctx else None,
        "alert_url": ctx.url if ctx else "",
        "alert_summary": ctx.summary if ctx else "",
        "summary": "",
        "response_text": "",
        "changed_files": [],
        "diff": "",
        "diff_truncated": False,
        "error": None,
        "branch_name": None,
        "pr_url": None,
        "pr_number": None,
    }


def to_output(ctx: SecurityAlertContext, result: CodingResult) -> dict[str, Any]:
    error_kind: str | None = None
    if not result.success:
        error_kind = ERR_TIMEOUT if result.timed_out else ERR_EXECUTION
    return {
        **_base_output(ctx),
        "success": result.success,
        "error_kind": error_kind,
        "summary": result.summary,
        "response_text": _result_response_text(ctx, result),
        "changed_files": result.changed_files,
        "diff": result.diff,
        "diff_truncated": result.diff_truncated,
        "error": result.error,
    }


def with_ship_output(output: dict[str, Any], ship: ShipResult) -> dict[str, Any]:
    return {
        **output,
        "response_text": (
            f"Fixed {output.get('alert_type')} finding #{output.get('alert_number')} "
            f"in {output.get('owner')}/{output.get('repo')} and opened PR: {ship.pr.url}"
        ),
        "branch_name": ship.branch_name,
        "pr_url": ship.pr.url,
        "pr_number": ship.pr.number,
    }


def ship_error_output(output: dict[str, Any], exc: GitHubSecurityFixError) -> dict[str, Any]:
    return {
        **output,
        "success": False,
        "error_kind": exc.kind,
        "error": exc.message,
        "response_text": _single_line(exc.message),
        "branch_name": exc.branch_name,
    }


def error_output(
    kind: str, message: str, ctx: SecurityAlertContext | None = None
) -> dict[str, Any]:
    return {
        **_base_output(ctx),
        "error_kind": kind,
        "error": message,
        "response_text": _single_line(message),
    }


def _result_response_text(ctx: SecurityAlertContext, result: CodingResult) -> str:
    if not result.success:
        return _single_line(result.error or "No automatic patch was produced.")
    changed = ", ".join(result.changed_files) if result.changed_files else "the workspace"
    return f"Fixed {ctx.alert_type} finding #{ctx.number} in {ctx.owner}/{ctx.repo}; changed {changed}."


def _unsupported_without_fallback_message(reason: str | None) -> str:
    base = _single_line(reason or "No built-in local fixer produced a change.")
    suffix = (
        "Install and log in to a coding agent CLI (Pi, Claude Code, Codex, or Cursor) so "
        "OpenSRE can fix this finding automatically, or remediate it manually."
    )
    return f"{base} {suffix}".strip()


def _single_line(value: str) -> str:
    return " ".join(value.split())


def run_security_fix(
    *,
    owner: str | None = None,
    repo: str | None = None,
    alert_type: str | None = None,
    alert_number: int | None = None,
    alert_url: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
    open_pr: bool = False,
    github_token: str | None = None,
    confirm_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    with ExitStack() as workspaces:
        ws = workspace or coding_workspace()
        ctx: SecurityAlertContext | None = None
        try:
            coding_available, _coding_detail = verify_coding_agent()
            ctx = gather_security_alert_context(
                owner=owner,
                repo=repo,
                alert_type=alert_type,
                alert_number=alert_number,
                alert_url=alert_url,
                workspace=ws,
                github_token=github_token,
                prefer_builtin_local_fix=not coding_available,
            )
            ws = str(
                workspaces.enter_context(
                    repair_workspace(
                        ctx.owner,
                        ctx.repo,
                        workspace=workspace if open_pr and ((owner and repo) or alert_url) else ws,
                        token=resolve_github_token(github_token),
                        target=f"security:{ctx.alert_type}:{ctx.number}",
                    )
                )
            )
            if open_pr:
                ensure_ship_ready(ws, github_token=github_token)
            require_confirmation(
                confirm_fn,
                (
                    f"Apply an OpenSRE fix in {ws} for "
                    f"{ctx.owner}/{ctx.repo} {ctx.alert_type} finding #{ctx.number}? [y/N] "
                ),
            )
        except (GitHubSecurityFixError, GitCommandError) as exc:
            return error_output(exc.kind, exc.message, ctx)

        baseline = pre_coding_changes(ws) if open_pr else {}
        result = run_fix(ctx, ws, model)
        output = to_output(ctx, result)
        if not (open_pr and result.success):
            return output

        try:
            require_confirmation(
                confirm_fn,
                (
                    "Commit the changed files to a new opensre/github-security-fix-* branch, "
                    "push it, and open a GitHub pull request? [y/N] "
                ),
            )
            ship = run_ship(ctx, result, ws, baseline=baseline, github_token=github_token)
        except GitHubSecurityFixError as exc:
            return ship_error_output(output, exc)
        return with_ship_output(output, ship)


__all__ = [
    "SOURCE",
    "ensure_cli_ready",
    "ensure_ship_ready",
    "error_output",
    "pre_coding_changes",
    "require_confirmation",
    "run_fix",
    "run_security_fix",
    "run_ship",
    "ship_error_output",
    "to_output",
    "with_ship_output",
]
