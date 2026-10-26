"""Error model for the merge-conflict resolution tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from integrations.git import MergeConflicts

# Stable failure categories surfaced in the tool's ``error_kind`` output field.
# Git failures pass their own ``GitCommandError.kind`` through unchanged.
ERR_NO_MERGE_IN_PROGRESS = "no_merge_in_progress"
ERR_CLI_UNAVAILABLE = "cli_unavailable"
ERR_TIMEOUT = "timeout"
ERR_EXECUTION = "execution_error"
ERR_CONFLICTS_REMAIN = "conflicts_remain"
ERR_MERGE_ABANDONED = "merge_abandoned"
ERR_CONFIRMATION_DENIED = "confirmation_denied"
ERR_CANCELLED = "cancelled"
ERR_AWAITING_DECISIONS = "awaiting_decisions"


class ResolveMergeError(Exception):
    """An expected, user-actionable failure with a stable ``kind``.

    ``unresolved`` names the conflicted files still waiting for a decision,
    ``summary`` keeps what the coding agent reported, and ``conflicts`` is the
    snapshot of the stopped merge, so the caller can still show the hunks side
    by side even though the merge did not complete (``rendered`` says it
    already did).
    """

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        unresolved: tuple[str, ...] = (),
        summary: str = "",
        conflicts: MergeConflicts | None = None,
        rendered: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.unresolved = unresolved
        self.summary = summary
        self.conflicts = conflicts
        self.rendered = rendered
