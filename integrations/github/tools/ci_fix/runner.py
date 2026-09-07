"""Lifecycle for fixing failing GitHub CI and pushing a repair or PR branch."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
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
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.tools.ci_fix.base_merge import BaseMergeResult, merge_base_into_head
from integrations.github.tools.ci_fix.context import (
    CI_TARGET_BRANCH,
    CiFixContext,
    gather_branch_ci_fix_context,
    gather_ci_fix_context,
)
from integrations.github.tools.ci_fix.errors import (
    ERR_CHECKS_FAILED,
    ERR_CHECKS_SUPERSEDED,
    ERR_CHECKS_TIMEOUT,
    ERR_CONFIRMATION_DENIED,
    ERR_EXECUTION,
    ERR_GITHUB_TOKEN,
    ERR_INVALID_INPUT,
    ERR_MERGE_CONFLICT,
    ERR_REPO_MISMATCH,
    ERR_REPO_SCOPE,
    ERR_TIMEOUT,
    GitHubCiFixError,
)
from integrations.github.tools.ci_fix.ship import PushResult, checkout_target_branch, push_ci_fix
from integrations.github.tools.ci_fix.verification import (
    DEFAULT_CHECK_WAIT_SECONDS,
    CheckState,
    CheckVerification,
    wait_for_branch_checks,
    wait_for_pr_checks,
)
from integrations.github.tools.ci_fix.worktree import (
    BranchWorktree,
    cleanup_branch_worktree,
    create_branch_worktree,
)

SOURCE: Final = "github"
_YES = {"y", "yes"}


def resolve_workspace(workspace: str | None) -> str:
    """Resolve the workspace once for the full run."""
    return workspace or coding_workspace()


def ensure_workspace_ready(workspace: str, owner: str, repo: str) -> None:
    """Require a git checkout whose origin matches the target repository."""
    try:
        ensure_git_repo(workspace)
    except GitCommandError as exc:
        raise GitHubCiFixError(exc.kind, exc.message) from exc
    detected = detect_git_remote_repo_scope(workspace)
    if detected is None:
        raise GitHubCiFixError(
            ERR_REPO_SCOPE,
            "Could not determine the GitHub owner/repo from the workspace's origin remote; no push was made.",
        )
    detected_owner, detected_repo = detected
    if (detected_owner.lower(), detected_repo.lower()) != (owner.lower(), repo.lower()):
        raise GitHubCiFixError(
            ERR_REPO_MISMATCH,
            (
                f"Workspace origin is {detected_owner}/{detected_repo}, "
                f"but the CI fix targets {owner}/{repo}; no push was made."
            ),
        )


def ensure_push_ready(github_token: str | None = None) -> None:
    if not resolve_github_token(github_token):
        raise GitHubCiFixError(
            ERR_GITHUB_TOKEN,
            "A GitHub token is required to push CI fixes; no push was made.",
        )


def pre_coding_changes(workspace: str) -> dict[str, str]:
    """Fingerprint already-dirty files so pushing excludes untouched WIP."""
    try:
        return file_fingerprints(workspace, changed_paths(workspace))
    except GitCommandError:
        return {}


def run_fix(ctx: CiFixContext, workspace: str, model: str | None) -> CodingResult:
    return _run_coding(
        ctx.task,
        workspace,
        model,
        unavailable=(
            f"Found failing CI checks on {ctx.target_label}, "
            "but no configured coding agent is ready; no push was made."
        ),
    )


def resolve_merge_conflicts(
    ctx: CiFixContext, workspace: str, model: str | None
) -> Callable[[str], CodingResult]:
    """Coding-agent runner for the conflicts of merging the base into the PR head."""

    def resolve(task: str) -> CodingResult:
        return _run_coding(
            task,
            workspace,
            model,
            unavailable=(
                f"Merging {ctx.base_branch} into {ctx.head_branch} has conflicts, "
                "but no configured coding agent is ready to resolve them"
            ),
        )

    return resolve


def _run_coding(task: str, workspace: str, model: str | None, *, unavailable: str) -> CodingResult:
    available, _detail = verify_coding_agent()
    if not available:
        return CodingResult(success=False, summary="", error=unavailable, returncode=-1)
    return run_coding_task(
        task,
        workspace=workspace,
        model=model or coding_model(),
        timeout_sec=coding_timeout_seconds(),
    )


def require_confirmation(
    confirm_fn: Callable[[str], str] | None,
    prompt: str,
) -> None:
    """Ask for local confirmation when a shell confirmation function is present."""
    if confirm_fn is None:
        return
    answer = confirm_fn(prompt)
    if answer.strip().lower() not in _YES:
        raise GitHubCiFixError(ERR_CONFIRMATION_DENIED, "User denied the requested CI fix.")


def _base_output(ctx: CiFixContext | None = None) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "success": False,
        "error_kind": None,
        "owner": ctx.owner if ctx else "",
        "repo": ctx.repo if ctx else "",
        "target_type": ctx.target_kind if ctx else "",
        "target_branch": ((ctx.target_branch or ctx.base_branch or ctx.head_branch) if ctx else ""),
        "pr_number": ctx.number if ctx else None,
        "pr_url": ctx.url if ctx and ctx.number is not None else "",
        "target_url": ctx.url if ctx else "",
        "base_branch": ctx.base_branch if ctx else "",
        "head_branch": ctx.head_branch if ctx else "",
        "failing_checks": [check.name for check in ctx.failing_checks] if ctx else [],
        "summary": "",
        "response_text": "",
        "changed_files": [],
        "diff": "",
        "diff_truncated": False,
        "error": None,
        "branch_name": None,
        "checks_state": None,
        "check_names": [],
        "merged_base_branch": "",
        "resolved_conflicts": [],
    }


def to_output(
    ctx: CiFixContext, result: CodingResult, merge: BaseMergeResult | None = None
) -> dict[str, Any]:
    error_kind: str | None = None
    if not result.success:
        error_kind = ERR_TIMEOUT if result.timed_out else ERR_EXECUTION
    base = with_merge_output(_base_output(ctx), merge) if merge else _base_output(ctx)
    return {
        **base,
        "success": result.success,
        "error_kind": error_kind,
        "summary": result.summary,
        "response_text": _result_response_text(ctx, result),
        "changed_files": result.changed_files,
        "diff": result.diff,
        "diff_truncated": result.diff_truncated,
        "error": result.error,
    }


def with_merge_output(output: dict[str, Any], merge: BaseMergeResult) -> dict[str, Any]:
    return {
        **output,
        "merged_base_branch": merge.base_branch,
        "resolved_conflicts": list(merge.resolved_files),
    }


def with_push_output(
    output: dict[str, Any],
    push: PushResult,
    verification: CheckVerification,
) -> dict[str, Any]:
    owner = str(output.get("owner") or "")
    repo = str(output.get("repo") or "")
    number = output.get("pr_number")
    target_branch = str(output.get("target_branch") or output.get("base_branch") or "")
    branch_target = output.get("target_type") == CI_TARGET_BRANCH or number is None
    target = (
        f"{owner}/{repo} branch {target_branch}" if branch_target else f"{owner}/{repo}#{number}"
    )
    checks_noun = "branch checks" if branch_target else "PR checks"
    result = {
        **output,
        "branch_name": push.branch_name,
        "changed_files": push.changed_files,
        "checks_state": verification.state.value,
        "check_names": list(verification.check_names),
    }
    if verification.state is CheckState.PASSED:
        return {
            **result,
            "response_text": (
                f"Fixed failing CI for {target}, {_merge_phrase(output)}pushed {push.branch_name}, "
                f"and all {checks_noun} passed."
            ),
        }
    if verification.state is CheckState.CONFLICTED:
        base_branch = str(output.get("base_branch") or "the base branch")
        return {
            **result,
            "success": False,
            "error_kind": ERR_MERGE_CONFLICT,
            "error": (
                f"GitHub will not start {checks_noun} because {push.branch_name} "
                f"still conflicts with {base_branch}."
            ),
            "response_text": (
                f"Pushed a CI fix to {push.branch_name}, but GitHub will not start {checks_noun} "
                f"because the branch still conflicts with {base_branch}."
            ),
        }
    if verification.state is CheckState.FAILED:
        failing = ", ".join(verification.failing_checks) or "unknown checks"
        return {
            **result,
            "success": False,
            "error_kind": ERR_CHECKS_FAILED,
            "error": f"Post-push {checks_noun} failed: {failing}.",
            "response_text": (
                f"Pushed a CI fix to {push.branch_name}, but {checks_noun} are still failing: "
                f"{failing}."
            ),
        }
    if verification.state is CheckState.SUPERSEDED:
        expected = push.head_sha[:12]
        observed = verification.observed_head_sha[:12] or "another commit"
        return {
            **result,
            "success": False,
            "error_kind": ERR_CHECKS_SUPERSEDED,
            "error": (
                f"PR head changed from pushed commit {expected} to {observed} "
                "before verification finished."
            ),
            "response_text": (
                f"Pushed a CI fix to {push.branch_name}, but another commit replaced "
                f"{expected} before its checks finished; verification stopped."
            ),
        }
    return {
        **result,
        "success": False,
        "error_kind": ERR_CHECKS_TIMEOUT,
        "error": (f"Post-push {checks_noun} did not finish before the verification timeout."),
        "response_text": (
            f"Pushed a CI fix to {push.branch_name}, but {checks_noun} did not finish within "
            f"{DEFAULT_CHECK_WAIT_SECONDS // 60} minutes."
        ),
    }


def push_error_output(output: dict[str, Any], exc: GitHubCiFixError) -> dict[str, Any]:
    return {
        **output,
        "success": False,
        "error_kind": exc.kind,
        "error": exc.message,
        "response_text": _single_line(exc.message),
        "branch_name": exc.branch_name,
    }


def error_output(kind: str, message: str, ctx: CiFixContext | None = None) -> dict[str, Any]:
    return {
        **_base_output(ctx),
        "error_kind": kind,
        "error": message,
        "response_text": _single_line(message),
    }


def _result_response_text(ctx: CiFixContext, result: CodingResult) -> str:
    if not result.success:
        return _single_line(result.error or "No CI fix was produced; no push was made.")
    changed = ", ".join(result.changed_files) if result.changed_files else "the workspace"
    return f"Prepared a CI fix for {ctx.target_label}; changed {changed}."


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _merge_phrase(output: dict[str, Any]) -> str:
    base_branch = str(output.get("merged_base_branch") or "")
    if not base_branch:
        return ""
    resolved = [str(path) for path in output.get("resolved_conflicts") or []]
    if not resolved:
        return f"merged {base_branch}, "
    return f"merged {base_branch} (resolved conflicts in {', '.join(resolved)}), "


def _confirmation_prompt(ctx: CiFixContext) -> str:
    if ctx.is_branch_target:
        return (
            f"Fix failing CI for {ctx.target_label} in a separate git worktree, "
            "editing files, committing, and pushing a fresh repair branch? [y/N] "
        )
    if ctx.needs_base_merge:
        return (
            f"Fix CI for {ctx.target_label} by checking out {ctx.head_branch}, "
            f"merging {ctx.base_branch} into it and resolving conflicts, editing files, "
            "committing, and pushing to that branch? [y/N] "
        )
    return (
        f"Fix failing CI for {ctx.target_label} by checking out "
        f"{ctx.head_branch}, editing files, committing, and pushing to that branch? [y/N] "
    )


def run_ci_fix(
    *,
    owner: str | None = None,
    repo: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    branch: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
    github_token: str | None = None,
    confirm_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    ws = resolve_workspace(workspace)
    branch_name = (branch or "").strip()
    ctx: CiFixContext | None = None
    worktree: BranchWorktree | None = None
    run_workspace = ws
    try:
        if branch_name and (pr_number is not None or pr_url):
            raise GitHubCiFixError(
                ERR_INVALID_INPUT,
                "Pass either a PR selector or a branch, not both; no push was made.",
            )
        if branch_name:
            ctx = gather_branch_ci_fix_context(
                branch=branch_name,
                owner=owner,
                repo=repo,
                workspace=ws,
                github_token=github_token,
            )
        else:
            ctx = gather_ci_fix_context(
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                pr_url=pr_url,
                workspace=ws,
                github_token=github_token,
            )
        ensure_workspace_ready(ws, ctx.owner, ctx.repo)
        ensure_push_ready(github_token=github_token)
        require_confirmation(confirm_fn, _confirmation_prompt(ctx))
        if ctx.is_branch_target:
            worktree = create_branch_worktree(ws, ctx)
            run_workspace = worktree.path
            ctx = replace(ctx, head_branch=worktree.branch_name)
        else:
            checkout_target_branch(ws, ctx)
    except GitHubCiFixError as exc:
        return error_output(exc.kind, exc.message, ctx)

    output = _base_output(ctx)
    try:
        try:
            merge: BaseMergeResult | None = None
            if ctx.needs_base_merge:
                merge = merge_base_into_head(
                    run_workspace,
                    ctx,
                    baseline=pre_coding_changes(run_workspace),
                    resolve_conflicts=resolve_merge_conflicts(ctx, run_workspace, model),
                )
                output = with_merge_output(output, merge)
            baseline = pre_coding_changes(run_workspace)
            result = _fix_result(ctx, run_workspace, model, merge)
            output = to_output(ctx, result, merge)
            if not result.success:
                return output

            push = push_ci_fix(
                ctx=ctx,
                result=result,
                workspace=run_workspace,
                baseline=baseline,
                github_token=github_token,
                already_committed=merge is not None,
            )
        except GitHubCiFixError as exc:
            return push_error_output(output, exc)
        try:
            wait_for_checks = wait_for_branch_checks if ctx.is_branch_target else wait_for_pr_checks
            verification = wait_for_checks(
                ctx,
                github_token=github_token,
                expected_head_sha=push.head_sha,
            )
        except GitHubCiFixError as exc:
            return {
                **push_error_output(output, exc),
                "branch_name": push.branch_name,
                "changed_files": push.changed_files,
                "response_text": (
                    f"Pushed a CI fix to {push.branch_name}, but could not verify the new checks."
                ),
            }
        return with_push_output(output, push, verification)
    finally:
        if worktree is not None:
            cleanup_branch_worktree(ws, worktree)


def _fix_result(
    ctx: CiFixContext, workspace: str, model: str | None, merge: BaseMergeResult | None
) -> CodingResult:
    """Run the CI fix, or stand in for it when the base merge was the whole repair."""
    if ctx.failing_checks:
        return run_fix(ctx, workspace, model)
    summary = merge.summary if merge else ""
    return CodingResult(success=True, summary=summary)


__all__ = [
    "SOURCE",
    "ensure_push_ready",
    "ensure_workspace_ready",
    "error_output",
    "pre_coding_changes",
    "require_confirmation",
    "resolve_merge_conflicts",
    "resolve_workspace",
    "run_ci_fix",
    "run_fix",
    "with_merge_output",
    "with_push_output",
]
