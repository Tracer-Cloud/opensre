"""OpenSRE CLI — open-source SRE agent for automated incident investigation.

Enable shell tab-completion (add to your shell profile for persistence):

  bash:  eval "$(_OPENSRE_COMPLETE=bash_source opensre)"
  zsh:   eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
  fish:  _OPENSRE_COMPLETE=fish_source opensre | source
"""

from __future__ import annotations

import click

_SETUP_SERVICES = ["aws", "datadog", "grafana", "opensearch", "rds", "slack", "tracer"]
_VERIFY_SERVICES = ["aws", "datadog", "grafana", "slack", "tracer"]


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="tracer-agent-2026", prog_name="opensre")
def cli() -> None:
    """OpenSRE — open-source SRE agent for automated incident investigation and root cause analysis.

    \b
    Quick start:
      opensre onboard                        Configure LLM provider and integrations
      opensre investigate -i alert.json      Run RCA against an alert payload
      opensre integrations list              Show configured integrations

    \b
    Enable tab-completion (add to your shell profile):
      eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
    """


@cli.command()
def onboard() -> None:
    """Run the interactive onboarding wizard."""
    from app.cli.wizard import run_wizard

    raise SystemExit(run_wizard())


@cli.command()
@click.option(
    "--input",
    "-i",
    "input_path",
    default=None,
    type=click.Path(),
    help="Path to an alert file (.json, .md, .txt, …). Use '-' to read from stdin.",
)
@click.option("--input-json", default=None, help="Inline alert JSON string.")
@click.option(
    "--interactive",
    is_flag=True,
    help="Paste an alert JSON payload into the terminal.",
)
@click.option(
    "--print-template",
    type=click.Choice(["generic", "datadog", "grafana"]),
    default=None,
    help="Print a starter alert JSON template and exit.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(),
    help="Output JSON file (default: stdout).",
)
def investigate(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    print_template: str | None,
    output: str | None,
) -> None:
    """Run an RCA investigation against an alert payload."""
    argv: list[str] = []
    if input_path is not None:
        argv.extend(["--input", input_path])
    if input_json is not None:
        argv.extend(["--input-json", input_json])
    if interactive:
        argv.append("--interactive")
    if print_template is not None:
        argv.extend(["--print-template", print_template])
    if output is not None:
        argv.extend(["--output", output])

    from app.main import main as investigate_main

    raise SystemExit(investigate_main(argv))


@cli.group()
def integrations() -> None:
    """Manage local integration credentials."""


@integrations.command()
@click.argument("service", type=click.Choice(_SETUP_SERVICES))
def setup(service: str) -> None:
    """Set up credentials for a service."""
    from dotenv import load_dotenv

    load_dotenv(override=False)

    from app.integrations.cli import cmd_setup

    cmd_setup(service)


@integrations.command(name="list")
def list_cmd() -> None:
    """List all configured integrations."""
    from dotenv import load_dotenv

    load_dotenv(override=False)

    from app.integrations.cli import cmd_list

    cmd_list()


@integrations.command()
@click.argument("service", type=click.Choice(_SETUP_SERVICES))
def show(service: str) -> None:
    """Show details for a configured integration."""
    from dotenv import load_dotenv

    load_dotenv(override=False)

    from app.integrations.cli import cmd_show

    cmd_show(service)


@integrations.command()
@click.argument("service", type=click.Choice(_SETUP_SERVICES))
def remove(service: str) -> None:
    """Remove a configured integration."""
    from dotenv import load_dotenv

    load_dotenv(override=False)

    from app.integrations.cli import cmd_remove

    cmd_remove(service)


@integrations.command()
@click.argument("service", required=False, default=None, type=click.Choice(_VERIFY_SERVICES))
@click.option(
    "--send-slack-test", is_flag=True, help="Send a test message to the configured Slack webhook."
)
def verify(service: str | None, send_slack_test: bool) -> None:
    """Verify integration connectivity (all services, or a specific one)."""
    from dotenv import load_dotenv

    load_dotenv(override=False)

    from app.integrations.cli import cmd_verify

    cmd_verify(service, send_slack_test=send_slack_test)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    try:
        cli(args=argv, standalone_mode=True)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
