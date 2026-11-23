"""Which statements the Snowflake query tool will send.

The statement is written by the model, which reads untrusted text (logs,
alerts, issue bodies), and runs with every grant the stored token has. So it
is held to one statement that reads.
"""

from __future__ import annotations

import re

# A quoted value, a dollar-quoted value, or a comment. Quoted values come first
# so a ``--`` or ``;`` inside one is never read as a comment or a separator.
_QUOTED_OR_COMMENT = re.compile(
    r"'(?:[^'\\]|\\.|'')*'"
    r'|"(?:[^"]|"")*"'
    r"|\$\$.*?\$\$"
    r"|/\*.*?\*/"
    r"|--[^\n]*",
    re.DOTALL,
)
_COMMENT_OPENERS = ("--", "/*")
# Statements that read. Snowflake has no data-modifying CTE, so a statement
# that starts with one of these cannot write.
_READ_ONLY_STATEMENT = re.compile(r"\s*(?:select|with|show|describe|desc|explain)\b", re.IGNORECASE)


def statement_skeleton(statement: str) -> str:
    """``statement`` with comments removed and every quoted value emptied.

    What is left is only keywords, names and punctuation, so looking for a
    keyword or a ``;`` in it cannot be fooled by the contents of a value.
    """

    def _empty(match: re.Match[str]) -> str:
        return " " if match.group().startswith(_COMMENT_OPENERS) else "''"

    return _QUOTED_OR_COMMENT.sub(_empty, statement)


def read_only_statement_error(statement: str) -> str | None:
    """Why ``statement`` may not run, or None when it is one read-only statement."""
    skeleton = statement_skeleton(statement).strip().rstrip(";").strip()
    if not skeleton:
        return None
    if ";" in skeleton:
        return "Only one statement can run per call."
    if not _READ_ONLY_STATEMENT.match(skeleton):
        return "Only SELECT, WITH, SHOW, DESCRIBE and EXPLAIN statements can run here."
    return None


def has_limit(statement: str) -> bool:
    """Whether ``statement`` already carries its own ``LIMIT`` clause."""
    return " limit " in f" {statement_skeleton(statement).lower()} "


__all__ = ["has_limit", "read_only_statement_error", "statement_skeleton"]
