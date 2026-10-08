"""Recognise shell commands that only read, so a ``shell_run`` call can say so.

Conservative on purpose: a command is a read only when every part of it runs a
listed read-only program or a read-only ``git`` subcommand without an option
that writes a file or runs another program, nothing writes a file through
redirection or runs a substituted command, and no environment assignment
changes how a program behaves. Anything else keeps the tool's declared mutating
level.
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
        "df",
        "dirname",
        "du",
        "echo",
        "grep",
        "head",
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
#: Options that make an otherwise read-only program write a file or run another program.
_WRITING_OPTIONS: dict[str, tuple[str, ...]] = {
    "git": ("--output", "-O", "--open-files-in-pager"),
    "rg": ("--pre",),
}
#: ``git`` options allowed before the subcommand; any other (``-c``, ``--exec-path``)
#: can make git run a configured program.
_GIT_GLOBAL_FLAGS = frozenset({"--no-pager", "-P"})
_SEPARATORS = re.compile(r"&&|\|\||[|;&\n]")
#: Redirections that write no file: into another stream or into /dev/null itself.
_HARMLESS_REDIRECT = re.compile(r"\d?>&\d|\d?>\s*/dev/null(?![^\s;&|])")
_WRITE_OR_SUBSTITUTE = (">", "`", "$(", "<(")
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")


def _git_subcommand(args: list[str]) -> str:
    """The subcommand after ``git``'s own options; "" when an option could run a program."""
    index = 0
    while index < len(args):
        token = args[index]
        if token == "-C":
            index += 2
        elif token in _GIT_GLOBAL_FLAGS:
            index += 1
        elif token.startswith("-"):
            return ""
        else:
            return token
    return ""


def _segment_only_reads(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    if not tokens or _ENV_ASSIGNMENT.match(tokens[0]):
        return False
    program = tokens[0].rsplit("/", 1)[-1]
    writing = _WRITING_OPTIONS.get(program, ())
    if any(token.startswith(writing) for token in tokens[1:]):
        return False
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
