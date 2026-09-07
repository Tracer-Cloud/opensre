"""Bring the PR base branch into a conflicted PR head before fixing its CI."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from integrations.coding_agent import CodingResult
from integrations.git import (
    ConflictedPath,
    GitCommandError,
    abort_merge,
    commit_merge,
    describe_conflicts,
    fetch_remote_branch,
    file_fingerprints,
    head_sha,
    is_ancestor,
    merge_in_progress,
    merge_ref,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import ERR_MERGE_CONFLICT, GitHubCiFixError
from integrations.github.tools.ci_fix.ship import changed_since_baseline

# Lockfiles are regenerated from their manifest, never merged by hand.
_LOCKFILE_NAMES: Final = frozenset(
    {
        "Cargo.lock",
        "Gemfile.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)


@dataclass(frozen=True)
class BaseMergeResult:
    """Outcome of merging the base branch into the PR head."""

    base_branch: str
    commit_sha: str
    resolved_files: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if not self.resolved_files:
            return f"merged {self.base_branch}"
        return f"merged {self.base_branch}, resolving conflicts in {', '.join(self.resolved_files)}"


def merge_base_into_head(
    workspace: str,
    ctx: CiFixContext,
    *,
    baseline: Mapping[str, str],
    resolve_conflicts: Callable[[str], CodingResult],
) -> BaseMergeResult:
    """Merge ``origin/<base>`` into the checked-out PR head, resolving conflicts via the coding agent.

    A clean merge commits directly. Conflicts are handed to *resolve_conflicts*
    with the exact files; the merge is committed only when no conflict marker
    or unmerged path remains, and aborted otherwise so the branch is untouched.
    """
    base_ref = f"origin/{ctx.base_branch}"
    try:
        fetch_remote_branch(workspace, ctx.base_branch)
        if merge_ref(workspace, base_ref, message=_merge_message(ctx)):
            return BaseMergeResult(base_branch=ctx.base_branch, commit_sha=head_sha(workspace))
        conflicts = describe_conflicts(workspace, ours=ctx.head_branch, theirs=ctx.base_branch)
        conflicted_content = file_fingerprints(workspace, [c.path for c in conflicts])
    except GitCommandError as exc:
        raise GitHubCiFixError(exc.kind, exc.message, branch_name=ctx.head_branch) from exc

    result = resolve_conflicts(_resolution_task(ctx, conflicts))
    try:
        if not merge_in_progress(workspace):
            return _merge_finished_by_agent(workspace, ctx, base_ref, conflicts)
        blocked = _unresolved(workspace, conflicts, conflicted_content)
        if not result.success or blocked:
            abort_merge(workspace)
            raise _blocked_error(ctx, blocked or conflicts, result)
        stage_paths(workspace, changed_since_baseline(workspace, baseline=baseline))
        stage_paths(workspace, [conflict.path for conflict in conflicts])
        if unmerged_paths(workspace):
            abort_merge(workspace)
            raise _blocked_error(ctx, conflicts, result)
        sha = commit_merge(workspace)
    except GitCommandError as exc:
        abort_merge(workspace)
        raise GitHubCiFixError(exc.kind, exc.message, branch_name=ctx.head_branch) from exc
    return BaseMergeResult(
        base_branch=ctx.base_branch,
        commit_sha=sha,
        resolved_files=tuple(conflict.path for conflict in conflicts),
    )


def _merge_finished_by_agent(
    workspace: str,
    ctx: CiFixContext,
    base_ref: str,
    conflicts: list[ConflictedPath],
) -> BaseMergeResult:
    """Accept a merge the coding agent committed itself; refuse one it abandoned."""
    if is_ancestor(workspace, base_ref, "HEAD"):
        return BaseMergeResult(
            base_branch=ctx.base_branch,
            commit_sha=head_sha(workspace),
            resolved_files=tuple(conflict.path for conflict in conflicts),
        )
    raise GitHubCiFixError(
        ERR_MERGE_CONFLICT,
        (
            f"The coding agent abandoned the merge of {ctx.base_branch} into "
            f"{ctx.head_branch}; conflicts remain in {_names(conflicts)}. No push was made."
        ),
        branch_name=ctx.head_branch,
    )


def _unresolved(
    workspace: str,
    conflicts: list[ConflictedPath],
    conflicted_content: Mapping[str, str],
) -> list[ConflictedPath]:
    """Conflicted paths the agent left with markers or never touched.

    Delete/modify conflicts carry no markers, so an untouched file is judged by
    its content fingerprint being unchanged since the merge stopped.
    """
    paths = [conflict.path for conflict in conflicts]
    marked = set(paths_with_conflict_markers(workspace, paths))
    current = file_fingerprints(workspace, paths)
    return [
        c
        for c in conflicts
        if c.path in marked or current.get(c.path, "") == conflicted_content.get(c.path, "")
    ]


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


def _resolution_task(ctx: CiFixContext, conflicts: list[ConflictedPath]) -> str:
    lockfiles = [c.path for c in conflicts if c.path.rsplit("/", 1)[-1] in _LOCKFILE_NAMES]
    lines = [
        f"Resolve the merge conflicts from merging {ctx.base_branch} into {ctx.head_branch} "
        f"for {ctx.owner}/{ctx.repo} PR #{ctx.number}.",
        "",
        f"PR: {ctx.url}",
        f"Title: {ctx.title}",
        f"The merge of origin/{ctx.base_branch} is in progress in the workspace; "
        "do not abort, reset, or commit it.",
        "",
        "Conflicted files:",
        *(f"- {c.path}: {c.description}" for c in conflicts),
        "",
        "Keep both the PR's intent and every change from "
        f"{ctx.base_branch}; remove all conflict markers.",
    ]
    if lockfiles:
        lines.append(
            "Do not hand-edit lockfiles "
            f"({', '.join(lockfiles)}): take the {ctx.base_branch} side, then regenerate "
            "them from the resolved manifest with the project's package manager "
            "(for example `pnpm install --lockfile-only`, `npm install --package-lock-only`, "
            "`uv lock`, `poetry lock --no-update`, `cargo generate-lockfile`)."
        )
    lines.extend(
        [
            "Leave the resolved files in the working tree; OpenSRE stages and commits the merge.",
            "Finish with a concise summary of how each conflict was resolved.",
        ]
    )
    return "\n".join(lines)


def _merge_message(ctx: CiFixContext) -> str:
    return (
        f"Merge {ctx.base_branch} into {ctx.head_branch} for the CI fix\n\n"
        f"Generated by OpenSRE from {ctx.url}."
    )


def _names(conflicts: list[ConflictedPath]) -> str:
    return ", ".join(c.path for c in conflicts)


__all__ = ["BaseMergeResult", "merge_base_into_head"]
