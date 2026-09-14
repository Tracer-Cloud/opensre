"""Refuse git commands that would change a merge in progress behind the merge tool's back."""

from __future__ import annotations

import os
import re

from integrations.git import GitCommandError, merge_in_progress

_GIT_STATE_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)git\b(?:\s+-C\s+(?P<cwd>\S+))?(?:\s+-[-\w=]+)*\s+"
    r"(?P<verb>commit|push|checkout|switch|reset|merge|rebase|restore)\b"
)

_REFUSAL = (
    "A merge is in progress in {cwd}; git {verb} is not run from the shell while it is. "
    "Use resolve_merge_conflicts (it shows each conflict, asks the user per file, commits "
    "as OpenSRE Agent and pushes), or ask the user."
)


def git_refusal_during_merge(command: str, cwd: str | None = None) -> str | None:
    """The refusal for *command* when it would alter a merge in progress, else ``None``."""
    match = _GIT_STATE_COMMAND.search(command)
    if match is None:
        return None
    workspace = match.group("cwd") or cwd or os.getcwd()
    try:
        merging = merge_in_progress(workspace)
    except GitCommandError:
        return None
    if not merging:
        return None
    return _REFUSAL.format(cwd=workspace, verb=match.group("verb"))


__all__ = ["git_refusal_during_merge"]
