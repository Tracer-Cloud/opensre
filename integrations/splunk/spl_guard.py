"""Refuse SPL that could write data, send messages or read secrets.

Splunk log searches run model-supplied SPL, and the model's inputs include
untrusted alert and log text. Every command in the pipeline, including those
inside ``[ ... ]`` subsearches, is checked against commands that write
(``collect``, ``outputlookup``, ``delete``), send (``sendemail``,
``sendalert``), run code (``script``), reach other systems (``dbxquery``) or
read Splunk's own REST API (``rest``, which can return stored credentials).

A Splunk role without those capabilities remains the real boundary.
"""

from __future__ import annotations

import re

_BLOCKED_COMMANDS = frozenset(
    {
        "collect",
        "dbxoutput",
        "dbxquery",
        "delete",
        "dump",
        "map",
        "mcollect",
        "meventcollect",
        "outputcsv",
        "outputlookup",
        "outputtext",
        "rest",
        "run",
        "script",
        "sendalert",
        "sendemail",
        "tscollect",
    }
)
# Quoted strings, and ```...``` comments, which Splunk turns into whitespace
# before parsing, so a comment cannot hide the command that follows it.
_QUOTED_OR_COMMENT = re.compile(r'"(?:[^"\\]|\\.)*"|```.*?```', re.DOTALL)
# A command name follows the start of the search, a pipe, or a subsearch bracket.
_COMMAND = re.compile(r"(?:^|[|\[])\s*([A-Za-z_][A-Za-z0-9_]*)")


def _blank(match: re.Match[str]) -> str:
    return '""' if match.group(0).startswith('"') else " "


def unsafe_spl_problem(query: str) -> str | None:
    """Why ``query`` may not run as an investigation search, or ``None`` if it may."""
    code = _QUOTED_OR_COMMENT.sub(_blank, query)
    commands = {match.group(1).lower() for match in _COMMAND.finditer(code)}
    blocked = sorted(commands & _BLOCKED_COMMANDS)
    if blocked:
        return f"SPL command not allowed in investigation searches: {', '.join(blocked)}."
    return None


__all__ = ["unsafe_spl_problem"]
