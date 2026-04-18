"""OpenSRE CLI - open-source SRE agent for automated incident investigation.

Enable shell tab-completion (add to your shell profile for persistence):

  bash:  eval "$(_OPENSRE_COMPLETE=bash_source opensre)"
  zsh:   eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
  fish:  _OPENSRE_COMPLETE=fish_source opensre | source
"""

from __future__ import annotations

import os

import click
from dotenv import load_dotenv

from app.analytics.cli import capture_cli_invoked
from app.analytics.provider import capture_first_run_if_needed, shutdown_analytics
from app.cli.commands import register_commands
from app.cli.layout import RichGroup, render_landing
from app.cli.prompt_support import install_questionary_escape_cancel
from app.version import get_version


@click.group(
    cls=RichGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=get_version(), prog_name="opensre")
@click.option("--json", "-j", "json_output", is_flag=True, help="Emit machine-readable JSON output.")
@click.option("--verbose", is_flag=True, help="Print extra diagnostic information.")
@click.option("--debug", is_flag=True, help="Print debug-level logs and traces.")
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm all interactive prompts.")
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    verbose: bool,
    debug: bool,
    yes: bool,
) -> None:
    """OpenSRE - open-source SRE agent for automated incident investigation and root cause analysis."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug
    ctx.obj["yes"] = yes

    if verbose or debug:
        os.environ["TRACER_VERBOSE"] = "1"

    if ctx.invoked_subcommand is None:
        capture_cli_invoked()
        click.echo("🚧 OpenSRE is in Public Beta — features may change.", err=True)
        render_landing()
        raise SystemExit(0)

register_commands(cli)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    load_dotenv(override=False)
    install_questionary_escape_cancel()
    capture_first_run_if_needed()

    try:
        cli(args=argv, standalone_mode=True)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        if exc.code is not None:
            click.echo(exc.code, err=True)
            return 1
        return 0
    finally:
        shutdown_analytics(flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
