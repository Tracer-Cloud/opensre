"""Lifecycle for resolving the conflicts of a git merge with a coding agent.

Snapshot the stopped merge, hand the conflicted files to the configured coding
agent (via the neutral ``integrations/coding_agent`` seam), verify that no
marker or unmerged path remains, show each hunk side by side, then, once the
shell's approval policy allows it, stage and commit the merge and push the
branch to the one it tracks. Files the agent could not settle are reported and
the merge is left in progress, so the user decides them; nothing is aborted.
"""

from __future__ import annotations

from collections.abc import Callable
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
    MergeConflicts,
    changed_paths,
    compare_hunks,
    conclude_merge,
    conflict_resolution_task,
    current_branch,
    describe_resolutions,
    ensure_git_repo,
    file_fingerprints,
    head_sha,
    is_ancestor,
    is_git_repo,
    merge_conflicts,
    merge_head_name,
    merge_head_sha,
    merge_in_progress,
    merge_ref,
    paths_with_conflict_markers,
    push_head_to_upstream,
    unresolved_conflicts,
    upstream_branch,
)
from tools.cross_vendor.resolve_merge_conflicts.comparison import render_comparison
from tools.cross_vendor.resolve_merge_conflicts.errors import (
    ERR_CLI_UNAVAILABLE,
    ERR_CONFIRMATION_DENIED,
    ERR_CONFLICTS_REMAIN,
    ERR_EXECUTION,
    ERR_MERGE_ABANDONED,
    ERR_NO_MERGE_IN_PROGRESS,
    ERR_TIMEOUT,
    ResolveMergeError,
)

SOURCE: Final = "git"

# Asked to allow one action; True means the shell's policy (or the user) approved it.
Approve = Callable[[str], bool]


def resolve_merge(
    workspace: str | None,
    *,
    ref: str | None,
    model: str | None,
    instructions: str | None,
    console: Any = None,
    approve: Approve | None = None,
) -> dict[str, Any]:
    """Resolve the merge in *workspace*, then commit and push it once *approve* allows.

    With a *console*, each conflict hunk is painted side by side (ours, theirs,
    merged result) before approval is requested. Without *approve* (no shell
    policy to consult, as in an unattended run) the commit and push proceed.
    """
    ws = workspace or coding_workspace()
    try:
        return _resolve(
            ws, ref=ref, model=model, instructions=instructions, console=console, approve=approve
        )
    except ResolveMergeError as exc:
        rendered = _paint(console, ws, exc.conflicts)
        return _output(
            ws,
            success=False,
            error_kind=exc.kind,
            error=exc.message,
            unresolved=exc.unresolved,
            summary=exc.summary,
            rendered=rendered or exc.rendered,
        )


def _resolve(
    ws: str,
    *,
    ref: str | None,
    model: str | None,
    instructions: str | None,
    console: Any,
    approve: Approve | None,
) -> dict[str, Any]:
    try:
        ensure_git_repo(ws)
        branch = current_branch(ws) or "HEAD"
        already_merging = merge_in_progress(ws)
        if not already_merging:
            if not ref:
                raise ResolveMergeError(
                    ERR_NO_MERGE_IN_PROGRESS,
                    f"No merge is in progress in {ws}. Name the branch or commit to merge "
                    f"into {branch} and it will be merged first.",
                )
            if merge_ref(ws, ref, message=f"Merge {ref} into {branch}"):
                return _pushed_output(ws, branch, str(ref), head_sha(ws), approve=approve)
        theirs = merge_head_name(ws) if already_merging else str(ref)
        merging = merge_head_sha(ws)
        conflicts = merge_conflicts(ws, ours=branch, theirs=theirs)
        baseline = file_fingerprints(ws, changed_paths(ws))
        already_resolved = bool(conflicts.paths) and not paths_with_conflict_markers(
            ws, conflicts.names
        )
    except GitCommandError as exc:
        raise ResolveMergeError(exc.kind, exc.message) from exc

    if not conflicts.paths or already_resolved:
        summary = (
            "The conflicted files were already edited in the working tree."
            if conflicts.paths
            else ""
        )
        return _commit(ws, conflicts, baseline, summary=summary, console=console, approve=approve)

    result = _run_agent(conflicts, ws, model=model, merged_ref=theirs, instructions=instructions)
    try:
        if not merge_in_progress(ws):
            return _merge_finished_by_agent(ws, conflicts, merging, result, console=console)
        remaining = _unresolved(ws, conflicts)
    except GitCommandError as exc:
        raise ResolveMergeError(exc.kind, exc.message, conflicts=conflicts) from exc
    if not result.success:
        left = tuple(path for path, _why in remaining) or conflicts.names
        raise ResolveMergeError(
            ERR_TIMEOUT if result.timed_out else ERR_EXECUTION,
            f"The coding agent did not finish: {result.error or 'no detail'}. "
            f"Conflicts remain in {', '.join(left)}; the merge stays in progress in {ws}.",
            unresolved=left,
            summary=result.summary,
            conflicts=conflicts,
        )
    if remaining:
        raise ResolveMergeError(
            ERR_CONFLICTS_REMAIN,
            f"{len(remaining)} file(s) still need a person's decision: "
            f"{'; '.join(f'{path} ({why})' for path, why in remaining)}. "
            f"The merge stays in progress in {ws}; decide those files with the user "
            "and run the resolution again with their instructions.",
            unresolved=tuple(path for path, _why in remaining),
            summary=result.summary,
            conflicts=conflicts,
        )
    return _commit(
        ws, conflicts, baseline, summary=result.summary, console=console, approve=approve
    )


def _run_agent(
    conflicts: MergeConflicts,
    ws: str,
    *,
    model: str | None,
    merged_ref: str,
    instructions: str | None,
) -> CodingResult:
    ready, detail = verify_coding_agent()
    if not ready:
        raise ResolveMergeError(
            ERR_CLI_UNAVAILABLE,
            f"Conflicts in {', '.join(conflicts.names)} need a coding agent, but none is "
            f"ready: {detail}. The merge stays in progress in {ws}.",
            unresolved=conflicts.names,
        )
    context = (f"Instructions from the user: {instructions.strip()}",) if instructions else ()
    task = conflict_resolution_task(conflicts, merged_ref=merged_ref, context_lines=context)
    return run_coding_task(
        task,
        workspace=ws,
        model=model or coding_model(),
        timeout_sec=coding_timeout_seconds(),
    )


def _unresolved(ws: str, conflicts: MergeConflicts) -> list[tuple[str, str]]:
    return [(c.path, c.description) for c in unresolved_conflicts(ws, conflicts)]


def _merge_finished_by_agent(
    ws: str, conflicts: MergeConflicts, merging: str, result: CodingResult, *, console: Any
) -> dict[str, Any]:
    """Accept a merge the coding agent committed itself; report one it abandoned."""
    if merging and is_ancestor(ws, merging, "HEAD"):
        sha = head_sha(ws)
        return _output(
            ws,
            branch=conflicts.ours,
            merged=conflicts.theirs,
            commit_sha=sha,
            resolved=conflicts.names,
            resolutions=_resolutions(ws, sha, conflicts),
            summary=result.summary,
            rendered=_paint(console, ws, conflicts),
        )
    raise ResolveMergeError(
        ERR_MERGE_ABANDONED,
        f"The coding agent abandoned the merge of {conflicts.theirs} into {conflicts.ours}; "
        "no merge is in progress any more. Start the merge again.",
        summary=result.summary,
    )


def _commit(
    ws: str,
    conflicts: MergeConflicts,
    baseline: dict[str, str],
    *,
    summary: str,
    console: Any,
    approve: Approve | None,
) -> dict[str, Any]:
    """Show the resolution, ask to commit and push, then do both."""
    rendered = _paint(console, ws, conflicts)
    target = _push_target(ws, conflicts.ours)
    action = f"commit the merge of {conflicts.theirs} into {conflicts.ours} and push it to {target}"
    if approve is not None and not approve(action):
        raise ResolveMergeError(
            ERR_CONFIRMATION_DENIED,
            f"Not approved: {action}. The resolved files are in the working tree, unstaged; "
            f"the merge stays in progress in {ws} and nothing was committed or pushed.",
            summary=summary,
            rendered=rendered,
        )
    try:
        sha = conclude_merge(ws, conflicts, baseline=baseline)
    except GitCommandError as exc:
        raise ResolveMergeError(
            exc.kind,
            f"{exc.message} The merge stays in progress in {ws}.",
            summary=summary,
            rendered=rendered,
        ) from exc
    pushed_to, push_error = _push(ws)
    return _output(
        ws,
        branch=conflicts.ours,
        merged=conflicts.theirs,
        commit_sha=sha,
        resolved=conflicts.names,
        resolutions=_resolutions(ws, sha, conflicts),
        summary=summary,
        rendered=rendered,
        pushed_to=pushed_to,
        error_kind=push_error.kind if push_error else None,
        error=push_error.message if push_error else None,
    )


def _pushed_output(
    ws: str, branch: str, merged: str, sha: str, *, approve: Approve | None
) -> dict[str, Any]:
    """A merge git committed cleanly still needs approval before it is pushed."""
    target = _push_target(ws, branch)
    action = f"push the clean merge of {merged} into {branch} to {target}"
    if approve is not None and not approve(action):
        return _output(ws, branch=branch, merged=merged, commit_sha=sha)
    pushed_to, push_error = _push(ws)
    return _output(
        ws,
        branch=branch,
        merged=merged,
        commit_sha=sha,
        pushed_to=pushed_to,
        error_kind=push_error.kind if push_error else None,
        error=push_error.message if push_error else None,
    )


def _push(ws: str) -> tuple[str, GitCommandError | None]:
    try:
        return push_head_to_upstream(ws), None
    except GitCommandError as exc:
        return "", exc


def _push_target(ws: str, branch: str) -> str:
    try:
        return upstream_branch(ws) or f"origin/{branch}"
    except GitCommandError:
        return f"origin/{branch}"


def _resolutions(ws: str, sha: str, conflicts: MergeConflicts) -> tuple[str, ...]:
    return tuple(str(r) for r in describe_resolutions(ws, sha, conflicts))


def _paint(console: Any, ws: str, conflicts: MergeConflicts | None) -> bool:
    """Draw the side-by-side hunk comparison when a terminal console is available."""
    if console is None or conflicts is None or not conflicts.paths:
        return False
    render_comparison(
        console, compare_hunks(ws, conflicts), ours=conflicts.ours, theirs=conflicts.theirs
    )
    return True


def _output(
    ws: str,
    *,
    success: bool = True,
    branch: str = "",
    merged: str = "",
    commit_sha: str | None = None,
    resolved: tuple[str, ...] = (),
    resolutions: tuple[str, ...] = (),
    unresolved: tuple[str, ...] = (),
    summary: str = "",
    error_kind: str | None = None,
    error: str | None = None,
    rendered: bool = False,
    pushed_to: str = "",
) -> dict[str, Any]:
    committed = success and bool(commit_sha)
    return {
        "outcome": _outcome(
            committed,
            branch=branch,
            merged=merged,
            commit_sha=commit_sha,
            pushed_to=pushed_to,
            error=error,
        ),
        "resolutions": list(resolutions),
        "next_step": _next_step(
            committed,
            branch=branch,
            commit_sha=commit_sha,
            pushed_to=pushed_to,
            unresolved=unresolved,
        ),
        "success": success,
        "error_kind": error_kind,
        "error": error,
        "workspace": ws,
        "branch": branch,
        "merged": merged,
        "commit_sha": commit_sha,
        "pushed": bool(pushed_to),
        "pushed_to": pushed_to,
        "resolved_files": list(resolved),
        "unresolved_files": list(unresolved),
        "coding_agent_summary": summary,
        "merge_in_progress": _merge_still_in_progress(ws),
        "rendered_in_shell": rendered,
    }


def _outcome(
    committed: bool,
    *,
    branch: str,
    merged: str,
    commit_sha: str | None,
    pushed_to: str,
    error: str | None,
) -> str:
    """One sentence the caller can repeat verbatim; it outranks the coding agent's own account."""
    if committed and commit_sha and pushed_to:
        return (
            f"OpenSRE committed the merge of {merged} into {branch} as {commit_sha[:12]} and "
            f"pushed it to {pushed_to}; the pull request is updated."
        )
    if committed and commit_sha:
        detail = f": {error}" if error else ""
        return (
            f"OpenSRE committed the merge of {merged} into {branch} locally as {commit_sha[:12]} "
            f"but did not push it{detail}. The remote and any pull request still show the "
            "conflict."
        )
    return error or "The merge was not committed."


def _next_step(
    committed: bool,
    *,
    branch: str,
    commit_sha: str | None,
    pushed_to: str,
    unresolved: tuple[str, ...],
) -> str:
    """What the user does now; the caller should suggest it."""
    if committed and commit_sha and pushed_to:
        return "Watch the pull request's checks on the pushed commit."
    if committed and commit_sha:
        return (
            f"Review the merge with `git show {commit_sha[:12]}`, then push {branch} to update "
            "the pull request."
        )
    if unresolved:
        return (
            "Decide the unresolved files with the user (which side to keep, or how to combine "
            "them) and run the resolution again with those instructions."
        )
    return (
        "Review the resolved files; to finish, ask again to commit and push the merge, or say "
        "what should change."
    )


def _merge_still_in_progress(ws: str) -> bool:
    try:
        return is_git_repo(ws) and merge_in_progress(ws)
    except GitCommandError:
        return False


__all__ = ["SOURCE", "resolve_merge"]
