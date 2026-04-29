"""Explicit entrypoint for the OpenSRE interactive agent terminal.

Running bare ``opensre`` on a TTY already enters the REPL, but users often
prefer an explicit subcommand — it reads better in scripts, composes with
``--layout``, and avoids ambiguity with the landing-page fallback path.

``opensre agent`` overrides ``OPENSRE_INTERACTIVE`` and
``~/.opensre/config.yml`` — the user typed the command; they want the
terminal. A real TTY is still required; on piped/CI stdin we surface a
clear error instead of silently no-op'ing.
"""

from __future__ import annotations

import sys

import click

from app.analytics.cli import capture_cli_invoked


@click.command(name="agent")
@click.option(
    "--layout",
    type=click.Choice(["classic", "pinned"]),
    default=None,
    help="REPL layout: 'classic' (scrolling) or 'pinned' (fixed input bar). "
    "Overrides OPENSRE_LAYOUT env var and ~/.opensre/config.yml.",
)
def agent_command(layout: str | None) -> None:
    """Launch the interactive SRE agent terminal."""
    from app.cli.errors import OpenSREError
    from app.cli.repl import run_repl
    from app.cli.repl.config import ReplConfig

    capture_cli_invoked()

    if not sys.stdin.isatty():
        raise OpenSREError(
            "`opensre agent` needs an interactive terminal (TTY).",
            suggestion="Run `opensre agent` directly in your terminal, "
            "or use `opensre investigate` for non-interactive workflows.",
        )

    config = ReplConfig.load(cli_enabled=True, cli_layout=layout)
    raise SystemExit(run_repl(config=config))
