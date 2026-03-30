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
      opensre tests                          Browse and run inventoried tests
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


@cli.group(invoke_without_command=True)
@click.pass_context
def tests(ctx: click.Context) -> None:
    """Browse and run inventoried tests from the terminal."""
    if ctx.invoked_subcommand is not None:
        return

    from app.cli.tests.discover import load_test_catalog
    from app.cli.tests.interactive import run_interactive_picker

    raise SystemExit(run_interactive_picker(load_test_catalog()))


@tests.command(name="list")
@click.option(
    "--category",
    type=click.Choice(["all", "rca", "demo", "infra-heavy", "ci-safe"]),
    default="all",
    show_default=True,
    help="Filter the inventory by category tag.",
)
@click.option("--search", default="", help="Case-insensitive text filter.")
def list_tests(category: str, search: str) -> None:
    """List available tests and suites."""
    from app.cli.tests.discover import load_test_catalog

    def _echo_item(item, *, indent: int = 0) -> None:
        prefix = "  " * indent
        tag_text = f" [{', '.join(item.tags)}]" if item.tags else ""
        click.echo(f"{prefix}{item.id} - {item.display_name}{tag_text}")
        if item.description:
            click.echo(f"{prefix}  {item.description}")
        if item.children:
            for child in item.children:
                _echo_item(child, indent=indent + 1)

    catalog = load_test_catalog()
    for item in catalog.filter(category=category, search=search):
        _echo_item(item)


@tests.command()
@click.argument("test_id")
@click.option("--dry-run", is_flag=True, help="Print the selected command without running it.")
def run(test_id: str, dry_run: bool) -> None:
    """Run a test or suite by stable inventory id."""
    from app.cli.tests.runner import find_test_item, run_catalog_item

    item = find_test_item(test_id)
    if item is None:
        raise click.ClickException(f"Unknown test id: {test_id}")

    raise SystemExit(run_catalog_item(item, dry_run=dry_run))


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    try:
        cli(args=argv, standalone_mode=True)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
