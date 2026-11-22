"""Refuse Snowflake SQL that could do more than read.

The query-history tool runs a model-supplied statement, and the model's inputs
include untrusted alert and log text. Only a single read statement is allowed:
it must start with a read keyword, and no write, DDL, access-control or
procedure keyword may appear outside string literals and comments. Snowflake
``SYSTEM$`` functions are refused too, quoted or not, since several act on the
account (``SYSTEM$CANCEL_QUERY``, ``SYSTEM$ABORT_SESSION``), and so is
``IDENTIFIER()``, which can name a function from a string.

A read-only Snowflake role remains the real boundary; this check keeps the
tool from being the way around a role that can write.
"""

from __future__ import annotations

import re

_READ_KEYWORDS = frozenset({"select", "with", "show", "describe", "desc", "explain"})
# Statement-level commands (USE, SET, GET @stage, ...) already fail the first-word
# check; these catch writes nested after a read keyword, such as in a CTE.
_WRITE_KEYWORDS = frozenset(
    {
        "alter",
        "call",
        "copy",
        "create",
        "delete",
        "drop",
        "execute",
        "grant",
        "insert",
        "merge",
        "put",
        "revoke",
        "truncate",
        "undrop",
        "update",
    }
)
# Comments and quoted text, so keywords inside them are not mistaken for SQL.
_LITERALS_AND_COMMENTS = re.compile(
    r"'(?:[^'\\]|\\.|'')*'"  # single-quoted string
    r'|"(?:[^"]|"")*"'  # quoted identifier
    r"|\$\$.*?\$\$"  # dollar-quoted string
    r"|--[^\n]*"  # line comment
    r"|//[^\n]*"  # Snowflake line comment
    r"|/\*.*?\*/",  # block comment
    re.DOTALL,
)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def _blank(match: re.Match[str], quoted_names: list[str]) -> str:
    """Blank a literal or comment; record a quoted identifier's name first.

    Quoted identifiers are names, not data: ``"SYSTEM$CANCEL_QUERY"`` still
    calls the function, so its name is kept for inspection.
    """
    text = match.group(0)
    if text.startswith('"'):
        quoted_names.append(text[1:-1].replace('""', '"'))
    return " "


def read_only_sql_problem(statement: str) -> str | None:
    """Why ``statement`` is not a single read-only statement, or ``None`` if it is."""
    quoted_names: list[str] = []
    code = _LITERALS_AND_COMMENTS.sub(lambda m: _blank(m, quoted_names), statement)
    code = code.strip().rstrip(";").strip()
    if not code:
        return None
    if ";" in code:
        return "Only a single SQL statement is allowed."
    words = [word.lower() for word in _WORD.findall(code)]
    if not words or words[0] not in _READ_KEYWORDS:
        return "Only read statements (SELECT, WITH, SHOW, DESCRIBE, EXPLAIN) are allowed."
    # A keyword directly followed by "(" is a function call (TRUNCATE(n, 2),
    # INSERT(str, ...)); no write statement starts that way.
    statement_words = {
        match.group(0).lower()
        for match in _WORD.finditer(code)
        if not code[match.end() :].lstrip().startswith("(")
    }
    blocked = sorted(statement_words & _WRITE_KEYWORDS)
    if blocked:
        return f"Statement contains a write or control keyword: {', '.join(blocked).upper()}."
    quoted_system = any(name.strip().lower().startswith("system$") for name in quoted_names)
    if quoted_system or any(word.startswith("system$") for word in words):
        return "SYSTEM$ functions are not allowed."
    if "identifier" in words:
        # IDENTIFIER('...') resolves a name from a string, so it could name any function.
        return "IDENTIFIER() is not allowed."
    return None


__all__ = ["read_only_sql_problem"]
