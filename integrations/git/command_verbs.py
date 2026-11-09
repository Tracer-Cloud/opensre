"""Find the git subcommands a shell command line runs, for guards that refuse some of them."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator

SHELL_OPERATORS = frozenset({"&&", "||", "|", ";", "&"})
_GLOBAL_TAKES_VALUE = frozenset(
    {"-C", "-c", "--config-env", "--exec-path", "--git-dir", "--namespace", "--work-tree"}
)
# ``git`` as its own word (also ``/usr/bin/git``), not ``.git``, ``gitk`` or ``git-lfs``.
_GIT_WORD = re.compile(r"(?<![-\w.])git(?![-\w])")


def command_tokens(tail: str) -> list[str]:
    """Shell-split *tail*; unbalanced quotes (``git`` inside ``sh -c '…'``) fall back to whitespace."""
    try:
        return shlex.split(tail)
    except ValueError:
        return tail.replace("'", " ").replace('"', " ").split()


def git_invocations(command: str, default_cwd: str = "") -> Iterator[tuple[str, str]]:
    """Each ``git`` call in *command* as ``(workspace, verb)``; ``-C`` names the workspace."""
    for match in _GIT_WORD.finditer(command):
        workspace, verb = _workspace_and_verb(command_tokens(command[match.end() :]), default_cwd)
        if verb:
            yield workspace, verb


def _workspace_and_verb(tokens: list[str], default_cwd: str) -> tuple[str, str]:
    """The first positional after git's global options, or ``""`` when the call has none."""
    workspace = default_cwd
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in SHELL_OPERATORS:
            break
        if token in _GLOBAL_TAKES_VALUE and index + 1 < len(tokens):
            if token == "-C":
                workspace = tokens[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return workspace, token
    return workspace, ""


__all__ = ["SHELL_OPERATORS", "command_tokens", "git_invocations"]
