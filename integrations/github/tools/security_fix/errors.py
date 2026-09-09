"""Error model for the GitHub security alert fix tool."""

from __future__ import annotations

ERR_INVALID_INPUT = "invalid_input"
ERR_GITHUB_UNAVAILABLE = "github_unavailable"
ERR_ALERT_NOT_FOUND = "alert_not_found"
ERR_NO_AUTOFIXABLE_FINDING = "no_autofixable_finding"
ERR_FIX_ALREADY_OPEN = "fix_already_open"
ERR_UNSUPPORTED_ALERT_TYPE = "unsupported_alert_type"
ERR_REPO_SCOPE = "repo_scope_unresolved"
ERR_REPO_MISMATCH = "repo_mismatch"
ERR_CLI_UNAVAILABLE = "cli_unavailable"
ERR_CONFIRMATION_DENIED = "confirmation_denied"
ERR_TIMEOUT = "timeout"
ERR_EXECUTION = "execution_error"
ERR_GITHUB_TOKEN = "github_token_missing"
ERR_NO_CHANGES = "no_changes"
ERR_PR_FAILED = "pr_failed"


class GitHubSecurityFixError(Exception):
    """Expected, user-actionable failure with a stable ``kind``."""

    def __init__(self, kind: str, message: str, *, branch_name: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.branch_name = branch_name
