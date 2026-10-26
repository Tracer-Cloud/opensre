"""Session-store environment variable names."""

from __future__ import annotations

# Serialize writes to a session's JSONL file with an OS file lock so several
# gateway tasks (horizontal scale-out) can share one session on the mount
# without corrupting it. Off by default: a single-task deployment cannot hit the
# hazard and should not pay the per-write lock cost. Turn on when running N tasks.
OPENSRE_SESSION_FILE_LOCK_ENV = "OPENSRE_SESSION_FILE_LOCK"

__all__ = ["OPENSRE_SESSION_FILE_LOCK_ENV"]
