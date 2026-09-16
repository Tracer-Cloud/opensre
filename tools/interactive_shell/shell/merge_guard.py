"""Refuse shell commands that would change a merge, or the user's checkout, behind the tools' back."""

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

# ``gh`` as its own token — not ``ghk`` or a prefix of another word.
_GH_TOKEN = re.compile(r"(?<![-\w])gh(?![-\w])")
# Global flags that consume a following value. ``-h`` is ``--help``, not hostname.
_GH_VALUE_FLAGS = frozenset({"-R", "--repo", "--hostname"})
_PR_CHECKOUT_REFUSAL = (
    "gh pr checkout would switch the branch of the user's checkout in {cwd}; it is not run "
    "from the shell. Use resolve_merge_conflicts with pull_request set to the number or URL "
    "(it clones the pull request into a workspace of its own), or fix_github_pr_ci for a "
    "CI failure."
)

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


def pull_request_checkout_refusal(command: str, cwd: str | None = None) -> str | None:
    """The refusal for *command* when it checks a pull request out here, else ``None``."""
    for match in _GH_TOKEN.finditer(command):
        if _is_pr_checkout(_command_tokens(command[match.end() :])):
            return _PR_CHECKOUT_REFUSAL.format(cwd=cwd or os.getcwd())
    return None


def _mutating_git_ops(command: str, default_cwd: str) -> Iterator[tuple[str, str]]:
    for match in _GIT_TOKEN.finditer(command):
        tokens = _command_tokens(command[match.end() :])
        workspace, verb = _workspace_and_verb(tokens, default_cwd)
        if verb in _MUTATING_VERBS:
            yield workspace, verb


def _is_pr_checkout(tokens: list[str]) -> bool:
    """True when the tokens after ``gh`` name the ``pr checkout`` subcommand."""
    positionals: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _SHELL_OPERATORS:
            break
        if token == "--":
            rest = tokens[index + 1 :]
            stop = next((i for i, later in enumerate(rest) if later in _SHELL_OPERATORS), len(rest))
            positionals.extend(rest[:stop])
            break
        if token.startswith("-"):
            name, _, inline = token.partition("=")
            if not inline and name in _GH_VALUE_FLAGS and index + 1 < len(tokens):
                nxt = tokens[index + 1]
                if nxt not in _SHELL_OPERATORS and not nxt.startswith("-"):
                    index += 2
                    continue
            index += 1
            continue
        positionals.append(token)
        index += 1
    return len(positionals) >= 2 and positionals[0] == "pr" and positionals[1] == "checkout"


def _command_tokens(tail: str) -> list[str]:
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


__all__ = ["git_refusal_during_merge", "pull_request_checkout_refusal"]
