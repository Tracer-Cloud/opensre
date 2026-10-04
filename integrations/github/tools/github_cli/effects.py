"""Which ``gh`` invocations only read, so a call can report less than the tool's level.

``github_cli`` is declared mutating because it can write, but most of its calls
are reads. A call this module recognises as a read is reported as read-only on
its result; anything it does not recognise keeps the declared level.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from integrations.github.tools.github_cli.runner import positional_gh_tokens

#: Commands whose every subcommand only reads.
_READ_COMMANDS = frozenset({"search", "status"})
#: Subcommands that only read, under any command that has them.
_READ_SUBCOMMANDS = frozenset({"checks", "diff", "list", "status", "view"})
#: ``gh api`` flags that add request fields; without an explicit method they make it a POST.
_API_FIELD_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")
_API_METHOD_FLAGS = ("-X", "--method")
_READ_METHODS = frozenset({"GET", "HEAD"})
_GRAPHQL_MUTATION = re.compile(r"\bmutation\b", re.IGNORECASE)


def _flag_values(args: Sequence[str], names: Sequence[str]) -> list[str] | None:
    """Every value given to one of ``names``; ``None`` when one is attached in a form not parsed."""
    values: list[str] = []
    for index, token in enumerate(args):
        name, equals, inline = token.partition("=")
        if name in names:
            if equals:
                values.append(inline)
            elif index + 1 < len(args):
                values.append(args[index + 1])
        elif any(token.startswith(short) and len(token) > 2 for short in names if len(short) == 2):
            return None  # ``-XPOST`` / ``-fkey=value``
    return values


def _api_call_only_reads(args: Sequence[str], endpoint: str) -> bool:
    fields = _flag_values(args, _API_FIELD_FLAGS)
    methods = _flag_values(args, _API_METHOD_FLAGS)
    if fields is None or methods is None:
        return False
    if endpoint == "graphql":
        queries = [field.partition("=")[2] for field in fields if field.startswith("query=")]
        return bool(queries) and not any(
            query.startswith("@") or _GRAPHQL_MUTATION.search(query) for query in queries
        )
    if methods:
        return all(method.upper() in _READ_METHODS for method in methods)
    return not fields


def gh_call_only_reads(args: Sequence[str]) -> bool:
    """Whether ``gh <args>`` only reads; ``False`` whenever it might change something."""
    tokens = [str(arg) for arg in args]
    positionals = [token.lower() for token in positional_gh_tokens(tokens)]
    if not positionals:
        return False
    command = positionals[0]
    if command == "api":
        return _api_call_only_reads(tokens, positionals[1] if len(positionals) > 1 else "")
    if command in _READ_COMMANDS:
        return True
    return len(positionals) > 1 and positionals[1] in _READ_SUBCOMMANDS


__all__ = ["gh_call_only_reads"]
