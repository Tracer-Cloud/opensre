"""Linked git worktrees: isolate an automated edit from the caller's checkout."""

from __future__ import annotations

import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from integrations.git.errors import BRANCH_FAILED, GitCommandError
from integrations.git.local import _run_git


def linked_worktree_path(workspace: str, prefix: str) -> Path:
    """Return an unused sibling directory of ``workspace`` named ``<prefix>-<random>``."""
    parent = Path(workspace).expanduser().resolve().parent
    while True:
        candidate = parent / f"{prefix}-{uuid.uuid4().hex[:8]}"
        if not candidate.exists():
            return candidate


def add_detached_worktree(workspace: str, path: str, ref: str) -> None:
    """Create a linked worktree at ``path`` checked out detached at ``ref``.

    Detached so the caller can name the branch after inspecting the worktree
    (its HEAD is ``ref``); a failed add leaves no directory behind.
    """
    result = _run_git(workspace, "worktree", "add", "--detach", path, ref)
    if result.returncode != 0:
        _remove_path(Path(path))
        raise GitCommandError(
            BRANCH_FAILED,
            f"Could not create a linked worktree at {path} from {ref}: {result.stderr.strip()}",
        )


def remove_worktree(workspace: str, path: str, *, branch: str = "") -> None:
    """Best-effort removal of a linked worktree and, when given, its local branch."""
    with suppress(GitCommandError):
        result = _run_git(workspace, "worktree", "remove", "--force", path)
        if result.returncode != 0:
            _remove_path(Path(path))
            _run_git(workspace, "worktree", "prune")
    if branch.strip():
        with suppress(GitCommandError):
            _run_git(workspace, "branch", "-D", branch.strip())


def _remove_path(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


__all__ = ["add_detached_worktree", "linked_worktree_path", "remove_worktree"]
