"""Refuse git commands that would change a merge in progress behind the merge tool's back."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator

from integrations.git import GitCommandError, merge_in_progress

# First positional after git global options. These finish, abandon, or restack
# a stopped merge; inspection verbs (status, log, diff) are not listed.
_MUTATING_VERBS = frozenset(
    {
        "add",
        "am",
        "checkout",
        "cherry-pick",
        "commit",
        "merge",
        "push",
        "rebase",
        "reset",
        "restore",
        "revert",
        "stash",
        "switch",
    }
)
_GIT_GLOBAL_TAKES_VALUE = frozenset(
    {
        "-C",
        "-c",
        "--config-env",
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--work-tree",
    }
)
_SHELL_OPERATORS = frozenset({"&&", "||", "|", ";", "&"})
# ``git`` as its own token — not ``gitk``, ``git-commit``, or a prefix of another word.
_GIT_TOKEN = re.compile(r"(?<![-\w])git(?![-\w])")

_REFUSAL = (
    "A merge is in progress in {cwd}; git {verb} is not run from the shell while it is. "
    "Use resolve_merge_conflicts (it shows each conflict, asks the user per file, commits "
    "as OpenSRE Agent and pushes), or ask the user."
)


def git_refusal_during_merge(command: str, cwd: str | None = None) -> str | None:
    """The refusal for *command* when it would alter a merge in progress, else ``None``."""
    default_cwd = cwd or os.getcwd()
    for workspace, verb in _mutating_git_ops(command, default_cwd):
        try:
            merging = merge_in_progress(workspace)
        except GitCommandError:
            continue
        if merging:
            return _REFUSAL.format(cwd=workspace, verb=verb)
    return None


def _mutating_git_ops(command: str, default_cwd: str) -> Iterator[tuple[str, str]]:
    for match in _GIT_TOKEN.finditer(command):
        tokens = _argv_after_git(command[match.end() :])
        workspace, verb = _workspace_and_verb(tokens, default_cwd)
        if verb in _MUTATING_VERBS:
            yield workspace, verb


def _argv_after_git(tail: str) -> list[str]:
    try:
        return shlex.split(tail)
    except ValueError:
        # Unbalanced quotes (``git`` found inside ``sh -c '…'``); drop the quotes
        # so the verb is still visible.
        return tail.replace("'", " ").replace('"', " ").split()


def _workspace_and_verb(tokens: list[str], default_cwd: str) -> tuple[str, str | None]:
    workspace = default_cwd
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _SHELL_OPERATORS:
            return workspace, None
        if token == "-C" and index + 1 < len(tokens):
            workspace = tokens[index + 1]
            index += 2
            continue
        if token in _GIT_GLOBAL_TAKES_VALUE and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return workspace, token
    return workspace, None


__all__ = ["git_refusal_during_merge"]
