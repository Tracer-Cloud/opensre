"""Neutral error type for local git operations.

Kept vendor- and tool-agnostic so this package can be reused by any caller
(``fix_sentry_issue``, future GitLab flows, etc.) without depending on a tool's
error model. Callers map ``GitCommandError.kind`` onto their own error surface.
"""

from __future__ import annotations

# Stable failure categories a git operation can raise.
GIT_UNAVAILABLE = "git_unavailable"
NOT_A_GIT_REPO = "not_a_git_repo"
PROTECTED_BRANCH = "protected_branch"
BRANCH_FAILED = "branch_failed"
COMMIT_FAILED = "commit_failed"
PUSH_FAILED = "push_failed"
MERGE_FAILED = "merge_failed"


class GitCommandError(Exception):
    """A local git operation failed, with a stable ``kind`` for the caller to map."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
