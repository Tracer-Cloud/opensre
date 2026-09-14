"""Error model for the merge-conflict resolution tool."""

from __future__ import annotations

# Stable failure categories surfaced in the tool's ``error_kind`` output field.
# Git failures pass their own ``GitCommandError.kind`` through unchanged.
ERR_NO_MERGE_IN_PROGRESS = "no_merge_in_progress"
ERR_CLI_UNAVAILABLE = "cli_unavailable"
ERR_TIMEOUT = "timeout"
ERR_EXECUTION = "execution_error"
ERR_CONFLICTS_REMAIN = "conflicts_remain"
ERR_MERGE_ABANDONED = "merge_abandoned"


class ResolveMergeError(Exception):
    """An expected, user-actionable failure with a stable ``kind``.

    ``unresolved`` names the conflicted files still waiting for a decision and
    ``summary`` keeps what the coding agent reported, so the caller can show
    both even though the merge did not complete.
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        unresolved: tuple[str, ...] = (),
        summary: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.unresolved = unresolved
        self.summary = summary
