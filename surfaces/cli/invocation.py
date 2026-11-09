"""Argv classification, fast-path CLI answers, and stdio setup.

Pure helpers used by ``surfaces.cli.app`` before the full CLI is
bootstrapped. They take the Click command / argv explicitly so they carry no
dependency on the root group and stay trivially testable.

Fast paths (``--version``, ``--help``) must stay cheap: they answer before
:func:`surfaces.cli.startup.run` installs adapters.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import suppress

import click

from config.version import get_opensre_version


def ensure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr so the themed UI renders on legacy
    Windows consoles (cp1252) without UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")


def option_value_count(command: click.Command, token: str) -> int:
    for param in command.params:
        if not isinstance(param, click.Option):
            continue
        if token not in (*param.opts, *param.secondary_opts):
            continue
        if param.is_flag or param.count:
            return 0
        return max(param.nargs, 1)
    return 0


def _iter_argv_tokens(
    command: click.Command, argv: list[str]
) -> Iterator[tuple[click.Command, str]]:
    """Yield ``(command_in_scope, token)`` skipping option values and ``--`` tails.

    Tokens bound as values (``--allowed-tool --help``) are omitted so callers
    see the same flags and words Click would treat as options or operands.
    """
    current = command
    skip_values = 0
    for token in argv:
        if skip_values:
            skip_values -= 1
            continue
        if token == "--":
            return
        yield current, token
        if token.startswith("-") and token != "-":
            if "=" not in token:
                skip_values = option_value_count(current, token)
            continue
        if isinstance(current, click.Group):
            subcommand = current.get_command(click.Context(current), token)
            if subcommand is not None:
                current = subcommand


def resolve_command_parts(command: click.Command, argv: list[str]) -> list[str]:
    """Resolve nested Click command names without recording option values."""
    parts: list[str] = []
    for current, token in _iter_argv_tokens(command, argv):
        if token.startswith("-") and token != "-":
            continue
        if not isinstance(current, click.Group):
            continue

        subcommand = current.get_command(click.Context(current), token)
        if subcommand is None:
            break

        parts.append(subcommand.name or token)

    return parts


_HELP_FLAGS = frozenset({"-h", "--help"})

#: CLI tokens for the version fast path, shared by the classifier and printer.
_VERSION_FLAG = "--version"
_VERSION_COMMAND = "version"
_VERSION_JSON_FLAGS = frozenset({"--json", "-j"})


def is_fast_version_invocation(argv: list[str]) -> bool:
    """Return whether argv can be answered before bootstrapping the full CLI."""
    return (
        argv == [_VERSION_FLAG]
        or argv == [_VERSION_COMMAND]
        or (len(argv) == 2 and argv[0] in _VERSION_JSON_FLAGS and argv[1] == _VERSION_COMMAND)
    )


def is_fast_help_invocation(command: click.Command, argv: list[str]) -> bool:
    """Return whether argv only needs Click help, not product adapters.

    Help must not import kubernetes/boto3 via :func:`surfaces.cli.startup.run`.
    Subcommand help (``opensre doctor --help``) is the same: Click prints
    usage without running the command body.

    ``-h`` / ``--help`` bound as option values or after ``--`` are not help:
    Click still runs the command, so startup must run too.
    """
    return any(token in _HELP_FLAGS for _, token in _iter_argv_tokens(command, argv))


def print_fast_version(argv: list[str]) -> None:
    if argv == [_VERSION_FLAG]:
        click.echo(f"opensre, version {get_opensre_version()}")
        return

    import json
    import platform

    json_output = argv[0] in _VERSION_JSON_FLAGS
    payload = {
        "opensre": get_opensre_version(),
        "python": platform.python_version(),
        "os": platform.system().lower(),
        "arch": platform.machine(),
    }
    if json_output:
        click.echo(json.dumps(payload))
        return
    click.echo(f"opensre {payload['opensre']}")
    click.echo(f"Python  {payload['python']}")
    click.echo(f"OS      {payload['os']} ({payload['arch']})")
