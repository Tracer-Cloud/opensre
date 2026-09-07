"""Local git merge helpers: merge a ref, inspect conflicts, finish or abort."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from integrations.git.errors import COMMIT_FAILED, MERGE_FAILED, GitCommandError
from integrations.git.local import _run_git, _with_opensre_coauthor

# ``git ls-files -u`` stage numbers: 1 = merge base, 2 = ours (HEAD), 3 = theirs.
_STAGE_OURS = "2"
_STAGE_THEIRS = "3"
_CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")


@dataclass(frozen=True)
class ConflictedPath:
    """One unmerged path and which side changed or removed it."""

    path: str
    description: str


def fetch_remote_branch(workspace: str, branch: str, *, remote: str = "origin") -> None:
    """Update ``refs/remotes/<remote>/<branch>`` without touching local branches."""
    result = _run_git(workspace, "fetch", remote, f"{branch}:refs/remotes/{remote}/{branch}")
    if result.returncode != 0:
        raise GitCommandError(
            MERGE_FAILED,
            f"Could not fetch {remote}/{branch}: {result.stderr.strip()}",
        )


def merge_ref(workspace: str, ref: str, *, message: str) -> bool:
    """Merge *ref* into HEAD with a merge commit.

    Returns True when the merge committed cleanly. Returns False when git
    stopped on content conflicts, leaving the merge in progress for the caller
    to resolve. Any other failure aborts the merge and raises.
    """
    result = _run_git(
        workspace, "merge", "--no-ff", "--no-edit", "-m", _with_opensre_coauthor(message), ref
    )
    if result.returncode == 0:
        return True
    if unmerged_paths(workspace):
        return False
    abort_merge(workspace)
    raise GitCommandError(MERGE_FAILED, f"Could not merge {ref}: {result.stderr.strip()}")


def merge_in_progress(workspace: str) -> bool:
    result = _run_git(workspace, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    return result.returncode == 0


def unmerged_paths(workspace: str) -> list[str]:
    """Paths still carrying unresolved index stages."""
    result = _run_git(workspace, "diff", "--name-only", "--diff-filter=U", "-z")
    return [path for path in result.stdout.split("\0") if path]


def describe_conflicts(workspace: str, *, ours: str, theirs: str) -> list[ConflictedPath]:
    """Describe each unmerged path by which side changed or deleted it."""
    result = _run_git(workspace, "ls-files", "-u", "-z")
    stages: dict[str, set[str]] = {}
    for record in result.stdout.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) < 3 or not path:
            continue
        stages.setdefault(path, set()).add(fields[2])
    described: list[ConflictedPath] = []
    for path, present in stages.items():
        if _STAGE_OURS not in present:
            description = f"deleted on {ours}, changed on {theirs}"
        elif _STAGE_THEIRS not in present:
            description = f"changed on {ours}, deleted on {theirs}"
        else:
            description = f"changed on both {ours} and {theirs}"
        described.append(ConflictedPath(path=path, description=description))
    return described


def paths_with_conflict_markers(workspace: str, paths: Sequence[str]) -> list[str]:
    """Subset of *paths* whose working-tree content still holds conflict markers."""
    marked: list[str] = []
    for path in paths:
        file = os.path.join(workspace, path)
        if not os.path.isfile(file):
            continue
        with open(file, encoding="utf-8", errors="replace") as handle:
            if any(line.startswith(_CONFLICT_MARKERS) for line in handle):
                marked.append(path)
    return marked


def stage_paths(workspace: str, paths: Sequence[str]) -> None:
    """Stage modifications and deletions for exactly *paths*.

    A path already gone from both the index and the working tree (a deletion
    the resolver staged itself) has nothing left to stage and is skipped, since
    ``git add`` rejects a pathspec that matches nothing.
    """
    stageable = [
        p
        for p in paths
        if p in _indexed(workspace, paths) or os.path.lexists(os.path.join(workspace, p))
    ]
    if not stageable:
        return
    result = _run_git(workspace, "add", "-A", "--", *stageable)
    if result.returncode != 0:
        raise GitCommandError(MERGE_FAILED, f"git add failed: {result.stderr.strip()}")


def _indexed(workspace: str, paths: Sequence[str]) -> set[str]:
    result = _run_git(workspace, "ls-files", "-z", "--", *paths)
    return {path for path in result.stdout.split("\0") if path}


def commit_merge(workspace: str) -> str:
    """Conclude the in-progress merge with its prepared message; return the new HEAD.

    ``--cleanup=strip`` drops the ``# Conflicts:`` comment block git adds to the
    prepared message, which a non-editor commit would otherwise keep verbatim.
    """
    result = _run_git(workspace, "commit", "--no-edit", "--cleanup=strip")
    if result.returncode != 0:
        raise GitCommandError(COMMIT_FAILED, f"git commit failed: {result.stderr.strip()}")
    return head_sha(workspace)


def abort_merge(workspace: str) -> None:
    """Best-effort return to the pre-merge HEAD."""
    _run_git(workspace, "merge", "--abort")


def head_sha(workspace: str) -> str:
    result = _run_git(workspace, "rev-parse", "HEAD")
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise GitCommandError(COMMIT_FAILED, "Could not resolve HEAD.")
    return sha


def is_ancestor(workspace: str, ancestor: str, descendant: str) -> bool:
    result = _run_git(workspace, "merge-base", "--is-ancestor", ancestor, descendant)
    return result.returncode == 0


__all__ = [
    "ConflictedPath",
    "abort_merge",
    "commit_merge",
    "describe_conflicts",
    "fetch_remote_branch",
    "head_sha",
    "is_ancestor",
    "merge_in_progress",
    "merge_ref",
    "paths_with_conflict_markers",
    "stage_paths",
    "unmerged_paths",
]
