"""Local git client: vendor-neutral branch/commit/push/status helpers.

Public surface for callers that need safe local git operations (e.g. shipping a
code change as a branch + commit + push). Operations raise :class:`GitCommandError`
with a stable ``kind`` that callers map onto their own error model.
"""

from __future__ import annotations

from integrations.git.errors import (
    BRANCH_FAILED,
    COMMIT_FAILED,
    GIT_UNAVAILABLE,
    MERGE_FAILED,
    NOT_A_GIT_REPO,
    PROTECTED_BRANCH,
    PUSH_FAILED,
    GitCommandError,
)
from integrations.git.local import (
    assert_not_protected,
    changed_paths,
    checkout_branch,
    commit_paths,
    create_branch,
    current_branch,
    default_branch,
    ensure_git_repo,
    file_fingerprints,
    is_git_repo,
    push_branch,
    short_head,
)
from integrations.git.merge import (
    ConflictedPath,
    abort_merge,
    commit_merge,
    describe_conflicts,
    fetch_remote_branch,
    head_sha,
    is_ancestor,
    merge_in_progress,
    merge_ref,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)
from integrations.git.worktree_capture import WorktreeChanges, capture_worktree_changes

__all__ = [
    "BRANCH_FAILED",
    "COMMIT_FAILED",
    "GIT_UNAVAILABLE",
    "MERGE_FAILED",
    "NOT_A_GIT_REPO",
    "PROTECTED_BRANCH",
    "PUSH_FAILED",
    "ConflictedPath",
    "GitCommandError",
    "WorktreeChanges",
    "abort_merge",
    "assert_not_protected",
    "capture_worktree_changes",
    "changed_paths",
    "checkout_branch",
    "commit_merge",
    "commit_paths",
    "create_branch",
    "current_branch",
    "default_branch",
    "describe_conflicts",
    "ensure_git_repo",
    "fetch_remote_branch",
    "file_fingerprints",
    "head_sha",
    "is_ancestor",
    "is_git_repo",
    "merge_in_progress",
    "merge_ref",
    "paths_with_conflict_markers",
    "push_branch",
    "short_head",
    "stage_paths",
    "unmerged_paths",
]
