"""Lifecycle for resolving the conflicts of a git merge with a coding agent.

Snapshot the stopped merge, hand the conflicted files to the configured coding
agent (via the neutral ``integrations/coding_agent`` seam), verify that no
marker or unmerged path remains, then stage and commit the merge. Files the
agent could not settle are reported and the merge is left in progress, so the
user decides them; nothing is aborted and nothing is pushed.
"""

from __future__ import annotations

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
    conclude_merge,
    conflict_resolution_task,
    current_branch,
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
    unresolved_conflicts,
)
from tools.cross_vendor.resolve_merge_conflicts.errors import (
    ERR_CLI_UNAVAILABLE,
    ERR_CONFLICTS_REMAIN,
    ERR_EXECUTION,
    ERR_MERGE_ABANDONED,
    ERR_NO_MERGE_IN_PROGRESS,
    ERR_TIMEOUT,
    ResolveMergeError,
)

SOURCE: Final = "git"


def resolve_merge(
    workspace: str | None,
    *,
    ref: str | None,
    model: str | None,
    instructions: str | None,
) -> dict[str, Any]:
    """Resolve and commit the merge in *workspace*; merge *ref* first when none is in progress."""
    ws = workspace or coding_workspace()
    try:
        return _resolve(ws, ref=ref, model=model, instructions=instructions)
    except ResolveMergeError as exc:
        return _output(
            ws,
            success=False,
            error_kind=exc.kind,
            error=exc.message,
            unresolved=exc.unresolved,
            summary=exc.summary,
        )


def _resolve(
    ws: str, *, ref: str | None, model: str | None, instructions: str | None
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
                return _output(ws, branch=branch, merged=ref, commit_sha=head_sha(ws))
        theirs = merge_head_name(ws) if already_merging else str(ref)
        merging = merge_head_sha(ws)
        conflicts = merge_conflicts(ws, ours=branch, theirs=theirs)
        baseline = file_fingerprints(ws, changed_paths(ws))
    except GitCommandError as exc:
        raise ResolveMergeError(exc.kind, exc.message) from exc

    if not conflicts.paths:
        return _commit(ws, conflicts, baseline, summary="")

    result = _run_agent(conflicts, ws, model=model, merged_ref=theirs, instructions=instructions)
    try:
        if not merge_in_progress(ws):
            return _merge_finished_by_agent(ws, conflicts, merging, result)
        remaining = _unresolved(ws, conflicts)
    except GitCommandError as exc:
        raise ResolveMergeError(exc.kind, exc.message) from exc
    if not result.success:
        left = tuple(path for path, _why in remaining) or conflicts.names
        raise ResolveMergeError(
            ERR_TIMEOUT if result.timed_out else ERR_EXECUTION,
            f"The coding agent did not finish: {result.error or 'no detail'}. "
            f"Conflicts remain in {', '.join(left)}; the merge stays in progress in {ws}.",
            unresolved=left,
            summary=result.summary,
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
        )
    return _commit(ws, conflicts, baseline, summary=result.summary)


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
    ws: str, conflicts: MergeConflicts, merging: str, result: CodingResult
) -> dict[str, Any]:
    """Accept a merge the coding agent committed itself; report one it abandoned."""
    if merging and is_ancestor(ws, merging, "HEAD"):
        return _output(
            ws,
            branch=conflicts.ours,
            merged=conflicts.theirs,
            commit_sha=head_sha(ws),
            resolved=conflicts.names,
            summary=result.summary,
        )
    raise ResolveMergeError(
        ERR_MERGE_ABANDONED,
        f"The coding agent abandoned the merge of {conflicts.theirs} into {conflicts.ours}; "
        "no merge is in progress any more. Start the merge again.",
        summary=result.summary,
    )


def _commit(
    ws: str, conflicts: MergeConflicts, baseline: dict[str, str], *, summary: str
) -> dict[str, Any]:
    try:
        sha = conclude_merge(ws, conflicts, baseline=baseline)
    except GitCommandError as exc:
        raise ResolveMergeError(
            exc.kind, f"{exc.message} The merge stays in progress in {ws}.", summary=summary
        ) from exc
    return _output(
        ws,
        branch=conflicts.ours,
        merged=conflicts.theirs,
        commit_sha=sha,
        resolved=conflicts.names,
        summary=summary,
    )


def _output(
    ws: str,
    *,
    success: bool = True,
    branch: str = "",
    merged: str = "",
    commit_sha: str | None = None,
    resolved: tuple[str, ...] = (),
    unresolved: tuple[str, ...] = (),
    summary: str = "",
    error_kind: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "error_kind": error_kind,
        "error": error,
        "workspace": ws,
        "branch": branch,
        "merged": merged,
        "commit_sha": commit_sha,
        "resolved_files": list(resolved),
        "unresolved_files": list(unresolved),
        "summary": summary,
        "merge_in_progress": _merge_still_in_progress(ws),
    }


def _merge_still_in_progress(ws: str) -> bool:
    try:
        return is_git_repo(ws) and merge_in_progress(ws)
    except GitCommandError:
        return False


__all__ = ["SOURCE", "resolve_merge"]
