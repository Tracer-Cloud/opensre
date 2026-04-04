"""OpenSRE CLI — open-source SRE agent for automated incident investigation.

Enable shell tab-completion (add to your shell profile for persistence):

  bash:  eval "$(_OPENSRE_COMPLETE=bash_source opensre)"
  zsh:   eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
  fish:  _OPENSRE_COMPLETE=fish_source opensre | source
"""

from __future__ import annotations

import sys
import time

import click
from dotenv import load_dotenv

from app.analytics.cli import (
    capture_command_completed,
    current_command_context,
    investigation_properties,
    onboard_properties,
    reset_command_context,
    set_command_context,
    update_command_context,
)
from app.analytics.provider import capture_first_run_if_needed
from app.version import get_version

# Heavy application imports are kept inside command functions so the CLI starts
# fast and so that load_dotenv() in main() runs before any app module reads env.

_SETUP_SERVICES = [
    "aws",
    "coralogix",
    "datadog",
    "grafana",
    "honeycomb",
    "mongodb",
    "opensearch",
    "rds",
    "slack",
    "tracer",
]
_VERIFY_SERVICES = ["aws", "coralogix", "datadog", "grafana", "honeycomb", "mongodb", "slack", "tracer"]


_ASCII_HEADER = """\
  ___  ____  _____ _   _ ____  ____  _____
 / _ \\|  _ \\| ____| \\ | / ___||  _ \\| ____|
| | | | |_) |  _| |  \\| \\___ \\| |_) |  _|
| |_| |  __/| |___| |\\  |___) |  _ <| |___
 \\___/|_|   |_____|_| \\_|____/|_| \\_\\_____|"""


def _render_help() -> None:
    from rich.console import Console
    from rich.text import Text

    console = Console(highlight=False)
    console.print()
    console.print(Text.assemble(("  Usage: "), ("opensre", "bold white"), (" [OPTIONS] COMMAND [ARGS]...")))
    console.print()
    console.print(Text.assemble(("  Commands:", "bold white")))
    for name, desc in [
        ("onboard",       "Run the interactive onboarding wizard."),
        ("investigate",   "Run an RCA investigation against an alert payload."),
        ("tests",         "Browse and run inventoried tests from the terminal."),
        ("integrations",  "Manage local integration credentials."),
        ("update",        "Check for a newer version and update if one is available."),
    ]:
        console.print(Text.assemble(("    ", ""), (f"{name:<16}", "bold cyan"), desc))
    console.print()
    console.print(Text.assemble(("  Options:", "bold white")))
    console.print(Text.assemble(("    ", ""), (f"{'--version':<16}", "bold cyan"), "Show the version and exit."))
    console.print(Text.assemble(("    ", ""), (f"{'-h, --help':<16}", "bold cyan"), "Show this message and exit."))
    console.print()


def _render_landing() -> None:
    from rich.console import Console
    from rich.text import Text

    console = Console(highlight=False)
    console.print()
    for line in _ASCII_HEADER.splitlines():
        console.print(Text.assemble(("  ", ""), (line, "bold cyan")))
    console.print()
    console.print(Text.assemble(
        ("  ", ""),
        "open-source SRE agent for automated incident investigation and root cause analysis",
    ))
    console.print()
    console.print(Text.assemble(("  Usage: "), ("opensre", "bold white"), (" [OPTIONS] COMMAND [ARGS]...")))
    console.print()
    console.print(Text.assemble(("  Quick start:", "bold white")))
    for cmd, desc in [
        ("opensre onboard",                   "Configure LLM provider and integrations"),
        ("opensre investigate -i alert.json", "Run RCA against an alert payload"),
        ("opensre tests",                     "Browse and run inventoried tests"),
        ("opensre integrations list",         "Show configured integrations"),
        ("opensre update",                    "Update to the latest version"),
    ]:
        console.print(Text.assemble(("    ", ""), (f"{cmd:<42}", "bold cyan"), desc))
    console.print()
    console.print(Text.assemble(("  Options:", "bold white")))
    console.print(Text.assemble(("    ", ""), (f"{'--version':<42}", "bold cyan"), "Show the version and exit."))
    console.print(Text.assemble(("    ", ""), (f"{'-h, --help':<42}", "bold cyan"), "Show this message and exit."))
    console.print()


class _RichGroup(click.Group):
    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:  # noqa: ARG002
        _render_help()


def _fallback_command(argv: list[str]) -> str:
    tokens = [arg for arg in argv if not arg.startswith("-")]
    if tokens:
        if tokens[0] in {"integrations", "tests"} and len(tokens) > 1:
            return f"{tokens[0]} {tokens[1]}"
        return tokens[0]
    if "--version" in argv:
        return "version"
    if any(arg in {"-h", "--help"} for arg in argv):
        return "help"
    return "opensre"


def _exit_code(value: object) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return 0
    return 1


@click.group(
    cls=_RichGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(version=get_version(), prog_name="opensre")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """OpenSRE — open-source SRE agent for automated incident investigation and root cause analysis.

    \b
    Quick start:
      opensre onboard                        Configure LLM provider and integrations
      opensre investigate -i alert.json      Run RCA against an alert payload
      opensre tests                          Browse and run inventoried tests
      opensre integrations list              Show configured integrations
      opensre health                         Check integration and agent setup status

    \b
    Enable tab-completion (add to your shell profile):
      eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
    """
    if ctx.invoked_subcommand is None:
        set_command_context("opensre")
        _render_landing()
        raise SystemExit(0)


@cli.command()
@click.option("--check", "check_only", is_flag=True, help="Report whether an update is available without installing.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def update(check_only: bool, yes: bool) -> None:
    """Check for a newer version and update if one is available."""
    from app.cli.update import run_update

    set_command_context("update", {"check_only": check_only})
    rc = run_update(check_only=check_only, yes=yes)
    raise SystemExit(rc)


@cli.command()
def onboard() -> None:
    """Run the interactive onboarding wizard."""
    from app.cli.wizard import run_wizard
    from app.cli.wizard.store import get_store_path, load_local_config

    set_command_context("onboard")
    try:
        exit_code = run_wizard()
    except Exception:
        raise
    if exit_code == 0:
        cfg = load_local_config(get_store_path())
        update_command_context(onboard_properties(cfg))
    raise SystemExit(exit_code)


@cli.command()
def health() -> None:
    """Show a quick health summary of the local agent setup."""
    from app.config import get_environment
    from app.integrations.store import STORE_PATH
    from app.integrations.verify import format_verification_results, verify_integrations

    set_command_context("health")
    results = verify_integrations()

    click.echo("")
    click.echo("OpenSRE Health")
    click.echo("")
    click.echo("CLI")
    click.echo(f"  environment: {get_environment().value}")
    click.echo(f"  integration store: {STORE_PATH}")
    click.echo(format_verification_results(results))


@cli.command()
@click.option(
    "--input", "-i", "input_path",
    default=None, type=click.Path(),
    help="Path to an alert file (.json, .md, .txt, …). Use '-' to read from stdin.",
)
@click.option("--input-json", default=None, help="Inline alert JSON string.")
@click.option("--interactive", is_flag=True, help="Paste an alert JSON payload into the terminal.")
@click.option(
    "--print-template",
    type=click.Choice(["generic", "datadog", "grafana", "honeycomb", "coralogix"]),
    default=None,
    help="Print a starter alert JSON template and exit.",
)
@click.option("--output", "-o", default=None, type=click.Path(), help="Output JSON file (default: stdout).")
def investigate(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    print_template: str | None,
    output: str | None,
) -> None:
    """Run an RCA investigation against an alert payload."""
    from app.main import main as investigate_main

    set_command_context(
        "investigate",
        investigation_properties(
            input_path=input_path,
            input_json=input_json,
            interactive=interactive,
            print_template=print_template,
            output=output,
        ),
    )
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

    try:
        exit_code = investigate_main(argv)
    except Exception:
        raise
    raise SystemExit(exit_code)


@cli.group()
def integrations() -> None:
    """Manage local integration credentials."""


@integrations.command()
@click.argument("service", required=False, default=None, type=click.Choice(_SETUP_SERVICES))
def setup(service: str | None) -> None:
    """Set up credentials for a service."""
    from app.integrations.cli import cmd_setup

    set_command_context("integrations setup", {"service": service or "prompt"})
    cmd_setup(service)


@integrations.command(name="list")
def list_cmd() -> None:
    """List all configured integrations."""
    from app.integrations.cli import cmd_list

    set_command_context("integrations list")
    cmd_list()


@integrations.command()
@click.argument("service", type=click.Choice(_SETUP_SERVICES))
def show(service: str) -> None:
    """Show details for a configured integration."""
    from app.integrations.cli import cmd_show

    set_command_context("integrations show", {"service": service})
    cmd_show(service)


@integrations.command()
@click.argument("service", type=click.Choice(_SETUP_SERVICES))
def remove(service: str) -> None:
    """Remove a configured integration."""
    from app.integrations.cli import cmd_remove

    set_command_context("integrations remove", {"service": service})
    cmd_remove(service)


@integrations.command()
@click.argument("service", required=False, default=None, type=click.Choice(_VERIFY_SERVICES))
@click.option("--send-slack-test", is_flag=True, help="Send a test message to the configured Slack webhook.")
def verify(service: str | None, send_slack_test: bool) -> None:
    """Verify integration connectivity (all services, or a specific one)."""
    from app.integrations.cli import cmd_verify

    set_command_context(
        "integrations verify",
        {
            "service": service or "all",
            "send_slack_test": send_slack_test,
        },
    )
    cmd_verify(service, send_slack_test=send_slack_test)


@cli.group(invoke_without_command=True)
@click.pass_context
def tests(ctx: click.Context) -> None:
    """Browse and run inventoried tests from the terminal."""
    if ctx.invoked_subcommand is not None:
        return

    from app.cli.tests.discover import load_test_catalog
    from app.cli.tests.interactive import run_interactive_picker

    set_command_context("tests")
    raise SystemExit(run_interactive_picker(load_test_catalog()))


@tests.command(name="synthetic")
@click.option("--scenario", default="", help="Pin to a single scenario directory, e.g. 001-replication-lag.")
@click.option("--json", "output_json", is_flag=True, help="Print machine-readable JSON results.")
@click.option(
    "--mock-grafana", is_flag=True, default=True, show_default=True,
    help="Serve fixture data via FixtureGrafanaBackend instead of real Grafana calls.",
)
def test_rds_synthetic(scenario: str, output_json: bool, mock_grafana: bool) -> None:
    """Run the synthetic RDS PostgreSQL RCA benchmark."""
    set_command_context(
        "tests synthetic",
        {
            "scenario": scenario or "all",
            "mock_grafana": mock_grafana,
            "output_json": output_json,
        },
    )
    argv: list[str] = []
    if scenario:
        argv.extend(["--scenario", scenario])
    if output_json:
        argv.append("--json")
    if mock_grafana:
        argv.append("--mock-grafana")

    from tests.synthetic.rds_postgres.run_suite import main as run_suite_main

    raise SystemExit(run_suite_main(argv))


@tests.command(name="list")
@click.option(
    "--category",
    type=click.Choice(["all", "rca", "demo", "infra-heavy", "ci-safe"]),
    default="all", show_default=True,
    help="Filter the inventory by category tag.",
)
@click.option("--search", default="", help="Case-insensitive text filter.")
def list_tests(category: str, search: str) -> None:
    """List available tests and suites."""
    from app.cli.tests.discover import load_test_catalog

    set_command_context("tests list", {"category": category, "search": bool(search)})

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

    set_command_context("tests run", {"test_id": test_id, "dry_run": dry_run})
    item = find_test_item(test_id)
    if item is None:
        raise click.ClickException(
    f"Unknown test id: {test_id}. Run 'opensre tests list' to see available test ids.")

    raise SystemExit(run_catalog_item(item, dry_run=dry_run))


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    load_dotenv(override=False)
    capture_first_run_if_needed()
    reset_command_context(_fallback_command(raw_args))
    started_at = time.perf_counter()
    exit_code = 0

    try:
        cli(args=argv, standalone_mode=True)
    except SystemExit as exc:
        exit_code = _exit_code(exc.code)
        if isinstance(exc.code, int):
            return exc.code
        if exc.code is not None:
            click.echo(exc.code, err=True)
            return 1
        return 0
    except Exception:
        exit_code = 1
        raise
    finally:
        context = current_command_context()
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        capture_command_completed(
            command=context.command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            properties=context.properties,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
