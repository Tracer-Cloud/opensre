"""Explicit entrypoint for the OpenSRE interactive agent terminal.

Running bare ``opensre`` on a TTY already enters the REPL, but users often
prefer an explicit subcommand — it reads better in scripts, composes with
``--layout``, and avoids ambiguity with the landing-page fallback path.

``opensre agent`` always starts the REPL regardless of the
``OPENSRE_INTERACTIVE`` env var or ``~/.opensre/config.yml`` — the user
typed the command; they want the terminal.
"""

from __future__ import annotations

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
    from app.cli.repl import run_repl
    from app.cli.repl.config import ReplConfig

    capture_cli_invoked()
    config = ReplConfig.load(cli_enabled=True, cli_layout=layout)
    raise SystemExit(run_repl(config=config))
