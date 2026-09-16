"""Bring the PR base branch into a conflicted PR head before fixing its CI."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from integrations.coding_agent import CodingResult, Progress
from integrations.git import (
    ConflictedPath,
    GitCommandError,
    MergeConflicts,
    abort_merge,
    compare_hunks,
    conclude_merge,
    conflict_resolution_task,
    fetch_remote_branch,
    head_sha,
    is_ancestor,
    merge_committed_by_resolver,
    merge_conflicts,
    merge_head_sha,
    merge_in_progress,
    merge_ref,
    render_overview,
    render_review,
    resolution_lines,
    unresolved_conflicts,
)
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import ERR_MERGE_CONFLICT, GitHubCiFixError


@dataclass(frozen=True)
class BaseMergeResult:
    """Outcome of merging the base branch into the PR head."""

    base_branch: str
    commit_sha: str
    resolved_files: tuple[str, ...] = ()
    resolutions: tuple[str, ...] = ()
    """One line per resolved file with the verdict of each conflict."""

    @property
    def summary(self) -> str:
        if not self.resolved_files:
            return f"merged {self.base_branch}"
        detail = "; ".join(self.resolutions) if self.resolutions else ", ".join(self.resolved_files)
        return f"merged {self.base_branch}, resolving conflicts in {detail}"


def base_has_new_commits(workspace: str, ctx: CiFixContext, *, token: str | None = None) -> bool:
    """True when ``origin/<base>`` holds commits the checked-out PR head lacks.

    Decided from git, not GitHub's merge state: a head that is merely behind
    is where a fix to shared files (lockfiles, manifests) creates the conflict
    GitHub only reports after the push.
    """
    try:
        fetch_remote_branch(workspace, ctx.base_branch, token=token)
        return not is_ancestor(workspace, f"origin/{ctx.base_branch}", "HEAD")
    except GitCommandError as exc:
        raise GitHubCiFixError(exc.kind, exc.message, branch_name=ctx.head_branch) from exc


def merge_base_into_head(
    workspace: str,
    ctx: CiFixContext,
    *,
    baseline: Mapping[str, str],
    resolve_conflicts: Callable[..., CodingResult],
    token: str | None = None,
    console: Any = None,
    on_progress: Progress | None = None,
) -> BaseMergeResult:
    """Merge ``origin/<base>`` into the checked-out PR head, resolving conflicts via the coding agent.

    A clean merge commits directly. Conflicts are handed to *resolve_conflicts*
    with the exact files (and *on_progress* for its steps); the merge is
    committed only when no conflict marker or unmerged path remains, and
    aborted otherwise so the branch is untouched. With a *console*, the
    conflicts are shown before resolving and the verdict per conflict after.
    """
    base_ref = f"origin/{ctx.base_branch}"
    try:
        fetch_remote_branch(workspace, ctx.base_branch, token=token)
        if merge_ref(
            workspace,
            base_ref,
            message=_merge_message(ctx),
            analytics_workflow="github_ci_fix",
        ):
            return BaseMergeResult(base_branch=ctx.base_branch, commit_sha=head_sha(workspace))
        conflicts = merge_conflicts(workspace, ours=ctx.head_branch, theirs=ctx.base_branch)
        merging = merge_head_sha(workspace)
    except GitCommandError as exc:
        raise GitHubCiFixError(exc.kind, exc.message, branch_name=ctx.head_branch) from exc

    if console is not None:
        render_overview(
            console,
            compare_hunks(workspace, conflicts),
            ours=ctx.head_branch,
            theirs=ctx.base_branch,
        )
    result = resolve_conflicts(_resolution_task(ctx, conflicts), on_progress=on_progress)
    try:
        if not merge_in_progress(workspace):
            return _merge_finished_by_agent(workspace, ctx, merging, conflicts)
        blocked = unresolved_conflicts(workspace, conflicts)
        if not result.success or blocked:
            abort_merge(workspace)
            raise _blocked_error(ctx, blocked or list(conflicts.paths), result)
        if console is not None:
            render_review(
                console,
                compare_hunks(workspace, conflicts),
                ours=ctx.head_branch,
                theirs=ctx.base_branch,
            )
        sha = conclude_merge(
            workspace,
            conflicts,
            baseline=baseline,
            analytics_workflow="github_ci_fix",
        )
    except GitCommandError as exc:
        abort_merge(workspace)
        raise GitHubCiFixError(exc.kind, exc.message, branch_name=ctx.head_branch) from exc
    return BaseMergeResult(
        base_branch=ctx.base_branch,
        commit_sha=sha,
        resolved_files=conflicts.names,
        resolutions=resolution_lines(workspace, sha, conflicts),
    )


def _merge_finished_by_agent(
    workspace: str,
    ctx: CiFixContext,
    merging: str,
    conflicts: MergeConflicts,
) -> BaseMergeResult:
    """Accept a merge the coding agent committed itself on the PR branch; refuse one it abandoned."""
    if merge_committed_by_resolver(workspace, conflicts, merging):
        return BaseMergeResult(
            base_branch=ctx.base_branch,
            commit_sha=head_sha(workspace),
            resolved_files=conflicts.names,
        )
    raise GitHubCiFixError(
        ERR_MERGE_CONFLICT,
        (
            f"The coding agent abandoned the merge of {ctx.base_branch} into "
            f"{ctx.head_branch}; conflicts remain in {', '.join(conflicts.names)}. "
            "No push was made."
        ),
        branch_name=ctx.head_branch,
    )


def _blocked_error(
    ctx: CiFixContext,
    blocked: list[ConflictedPath],
    result: CodingResult,
) -> GitHubCiFixError:
    decisions = "; ".join(f"{c.path} ({c.description})" for c in blocked)
    note = " ".join((result.error or result.summary or "").split()).rstrip(".")
    detail = f" Coding agent: {note}." if note else ""
    return GitHubCiFixError(
        ERR_MERGE_CONFLICT,
        (
            f"Merging {ctx.base_branch} into {ctx.head_branch} is blocked on "
            f"{len(blocked)} file(s) a person must decide: {decisions}.{detail} "
            "The merge was aborted and no push was made."
        ),
        branch_name=ctx.head_branch,
    )


def _resolution_task(ctx: CiFixContext, conflicts: MergeConflicts) -> str:
    return conflict_resolution_task(
        conflicts,
        merged_ref=f"origin/{ctx.base_branch}",
        context_lines=(
            f"This is {ctx.owner}/{ctx.repo} PR #{ctx.number}: {ctx.title}",
            f"PR: {ctx.url}",
        ),
    )


def _merge_message(ctx: CiFixContext) -> str:
    return (
        f"Merge {ctx.base_branch} into {ctx.head_branch} for the CI fix\n\n"
        f"Generated by OpenSRE from {ctx.url}."
    )


__all__ = ["BaseMergeResult", "base_has_new_commits", "merge_base_into_head"]
