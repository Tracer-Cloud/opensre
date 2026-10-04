"""Recognise shell commands that only read, so a ``shell_run`` call can say so.

Conservative on purpose: a command is a read only when every part of it runs a
listed read-only program or a read-only ``git`` subcommand, and nothing writes a
file through redirection or runs a substituted command. Anything else keeps the
tool's declared mutating level.
"""

from __future__ import annotations

import re
import shlex

#: Programs that only read or print, whatever their arguments.
_READ_PROGRAMS = frozenset(
    {
        "basename",
        "cat",
        "cd",
        "date",
        "df",
        "dirname",
        "du",
        "echo",
        "file",
        "grep",
        "head",
        "hostname",
        "id",
        "jq",
        "ls",
        "printf",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "stat",
        "tail",
        "tree",
        "uname",
        "wc",
        "which",
        "whoami",
    }
)
#: ``git`` subcommands that leave branches and the work tree alone; ``fetch``
#: moves only remote-tracking refs.
_GIT_READ_SUBCOMMANDS = frozenset(
    {
        "blame",
        "cat-file",
        "describe",
        "diff",
        "fetch",
        "for-each-ref",
        "grep",
        "log",
        "ls-files",
        "ls-remote",
        "merge-base",
        "rev-list",
        "rev-parse",
        "shortlog",
        "show",
        "show-ref",
        "status",
    }
)
_GIT_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_SEPARATORS = re.compile(r"&&|\|\||[|;&\n]")
#: Redirections that write no file: into another stream or into /dev/null.
_HARMLESS_REDIRECT = re.compile(r"\d?>&\d|\d?>\s*/dev/null\b")
_WRITE_OR_SUBSTITUTE = (">", "`", "$(", "<(")
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")


def _git_subcommand(args: list[str]) -> str:
    index = 0
    while index < len(args):
        token = args[index]
        if token in _GIT_OPTIONS_WITH_VALUE:
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return token
    return ""


def _segment_only_reads(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    while tokens and _ENV_ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return False
    program = tokens[0].rsplit("/", 1)[-1]
    if program == "git":
        return _git_subcommand(tokens[1:]) in _GIT_READ_SUBCOMMANDS
    return program in _READ_PROGRAMS


def shell_command_only_reads(command: str) -> bool:
    """Whether ``command`` only reads; ``False`` whenever it might change something."""
    cleaned = _HARMLESS_REDIRECT.sub(" ", command)
    if any(mark in cleaned for mark in _WRITE_OR_SUBSTITUTE):
        return False
    segments = [segment for segment in _SEPARATORS.split(cleaned) if segment.strip()]
    return bool(segments) and all(_segment_only_reads(segment) for segment in segments)


__all__ = ["shell_command_only_reads"]
