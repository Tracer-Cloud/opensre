"""Error model for the GitHub CI fix tool."""

from __future__ import annotations

ERR_CONFIRMATION_DENIED = "confirmation_denied"
ERR_CHECKS_FAILED = "checks_failed"
ERR_CHECKS_SUPERSEDED = "checks_superseded"
ERR_CHECKS_TIMEOUT = "checks_timeout"
ERR_EXECUTION = "execution_error"
ERR_GH_UNAVAILABLE = "github_cli_unavailable"
ERR_GITHUB_TOKEN = "github_token_missing"
ERR_INVALID_INPUT = "invalid_input"
ERR_MERGE_CONFLICT = "merge_conflict"
ERR_MERGE_DECISION = "merge_decision_required"
"""The coding agent finished the merge but left a choice only a person can make."""
ERR_MERGE_UNSETTLED = "merge_unsettled"
"""Internal: the agent finished but left a conflict untouched; the runner decides retry or ask."""
ERR_NO_CHANGES = "no_changes"
ERR_NO_FAILING_CHECKS = "no_failing_checks"
ERR_PR_NOT_FOUND = "pr_not_found"
ERR_PR_NOT_OPEN = "pr_not_open"
ERR_REPO_MISMATCH = "repo_mismatch"
ERR_REPO_SCOPE = "repo_scope_unresolved"
ERR_TIMEOUT = "timeout"
ERR_UNSUPPORTED_PR_BRANCH = "unsupported_pr_branch"


class GitHubCiFixError(Exception):
    """Expected, user-actionable failure with a stable ``kind``."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        branch_name: str | None = None,
        base_sha: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.branch_name = branch_name
        #: The base commit a failed merge tried to bring in, when one was known.
        self.base_sha = base_sha
