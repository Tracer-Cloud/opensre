"""OpenSRE CLI — open-source SRE agent for automated incident investigation.

Enable shell tab-completion (add to your shell profile for persistence):

  bash:  eval "$(_OPENSRE_COMPLETE=bash_source opensre)"
  zsh:   eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
  fish:  _OPENSRE_COMPLETE=fish_source opensre | source
"""

from __future__ import annotations

import json
import platform

import click
from dotenv import load_dotenv

from app.analytics.cli import (
    capture_cli_invoked,
    capture_integration_removed,
    capture_integration_setup_completed,
    capture_integration_setup_started,
    capture_integration_verified,
    capture_integrations_listed,
    capture_investigation_completed,
    capture_investigation_failed,
    capture_investigation_started,
    capture_onboard_completed,
    capture_onboard_failed,
    capture_onboard_started,
    capture_test_run_started,
    capture_test_synthetic_started,
    capture_tests_listed,
    capture_tests_picker_opened,
)
from app.analytics.provider import capture_first_run_if_needed, shutdown_analytics
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

_VERIFY_SERVICES = [
    "aws",
    "coralogix",
    "datadog",
    "grafana",
    "honeycomb",
    "mongodb",
    "opsgenie",
    "slack",
    "tracer",
    "vercel",
]


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
    console.print(
        Text.assemble(("  Usage: "), ("opensre", "bold white"), (" [OPTIONS] COMMAND [ARGS]..."))
    )
    console.print()
    console.print(Text.assemble(("  Commands:", "bold white")))
    for name, desc in [
        ("onboard", "Run the interactive onboarding wizard."),
        ("investigate", "Run an RCA investigation against an alert payload."),
        ("deploy", "Deploy OpenSRE to a cloud environment (EC2)."),
        ("remote", "Connect to a remote deployed agent."),
        ("tests", "Browse and run inventoried tests from the terminal."),
        ("integrations", "Manage local integration credentials."),
        ("health", "Check integration and agent setup status."),
        ("update", "Check for a newer version and update if one is available."),
        ("version", "Print detailed version, Python and OS info."),
    ]:
        console.print(Text.assemble(("    ", ""), (f"{name:<16}", "bold cyan"), desc))
    console.print()
    console.print(Text.assemble(("  Options:", "bold white")))
    console.print(
        Text.assemble(
            ("    ", ""), (f"{'--version':<16}", "bold cyan"), "Show the version and exit."
        )
    )
    console.print(
        Text.assemble(
            ("    ", ""), (f"{'-h, --help':<16}", "bold cyan"), "Show this message and exit."
        )
    )
    console.print()


def _render_landing() -> None:
    from rich.console import Console
    from rich.text import Text

    console = Console(highlight=False)
    console.print()
    for line in _ASCII_HEADER.splitlines():
        console.print(Text.assemble(("  ", ""), (line, "bold cyan")))
    console.print()
    console.print(
        Text.assemble(
            ("  ", ""),
            "open-source SRE agent for automated incident investigation and root cause analysis",
        )
    )
    console.print()
    console.print(
        Text.assemble(("  Usage: "), ("opensre", "bold white"), (" [OPTIONS] COMMAND [ARGS]..."))
    )
    console.print()
    console.print(Text.assemble(("  Quick start:", "bold white")))
    for cmd, desc in [
        ("opensre onboard", "Configure LLM provider and integrations"),
        ("opensre investigate -i alert.json", "Run RCA against an alert payload"),
        ("opensre deploy ec2", "Deploy investigation server on AWS EC2"),
        ("opensre remote --url <ip> health", "Check a remote deployed agent"),
        ("opensre tests", "Browse and run inventoried tests"),
        ("opensre integrations list", "Show configured integrations"),
        ("opensre health", "Check integration and agent setup status"),
        ("opensre update", "Update to the latest version"),
        ("opensre version", "Print detailed version, Python and OS info"),
    ]:
        console.print(Text.assemble(("    ", ""), (f"{cmd:<42}", "bold cyan"), desc))
    console.print()
    console.print(Text.assemble(("  Options:", "bold white")))
    console.print(
        Text.assemble(
            ("    ", ""), (f"{'--version':<42}", "bold cyan"), "Show the version and exit."
        )
    )
    console.print(
        Text.assemble(
            ("    ", ""), (f"{'-h, --help':<42}", "bold cyan"), "Show this message and exit."
        )
    )
    console.print()


class _RichGroup(click.Group):
    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:  # noqa: ARG002
        _render_help()


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
        capture_cli_invoked()
        _render_landing()
        raise SystemExit(0)


@cli.command()
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report whether an update is available without installing.",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def update(check_only: bool, yes: bool) -> None:
    """Check for a newer version and update if one is available."""
    from app.cli.update import run_update

    capture_cli_invoked()
    rc = run_update(check_only=check_only, yes=yes)
    raise SystemExit(rc)


@cli.command("version")
def version_cmd() -> None:
    """Print detailed version, Python and OS info."""
    capture_cli_invoked()
    ver = get_version()
    py = platform.python_version()
    os_name = platform.system().lower()
    arch = platform.machine()
    click.echo(f"opensre {ver}")
    click.echo(f"Python  {py}")
    click.echo(f"OS      {os_name} ({arch})")


@cli.group(invoke_without_command=True)
@click.pass_context
def onboard(ctx: click.Context) -> None:
    """Run the interactive onboarding wizard."""
    if ctx.invoked_subcommand is not None:
        return
    from app.cli.wizard import run_wizard
    from app.cli.wizard.store import get_store_path, load_local_config

    capture_onboard_started()
    try:
        exit_code = run_wizard()
    except Exception:
        capture_onboard_failed()
        raise
    if exit_code == 0:
        cfg = load_local_config(get_store_path())
        capture_onboard_completed(cfg)
    else:
        capture_onboard_failed()
    raise SystemExit(exit_code)


@onboard.command("local_llm")
def onboard_local_llm() -> None:
    """Zero-config local LLM setup via Ollama. No API key required."""
    from app.cli.local_llm.command import run_local_llm_setup

    capture_onboard_started()
    try:
        rc = run_local_llm_setup()
    except Exception:
        capture_onboard_failed()
        raise
    if rc == 0:
        from app.cli.wizard.store import get_store_path, load_local_config

        cfg = load_local_config(get_store_path())
        capture_onboard_completed(cfg)
    else:
        capture_onboard_failed()
    raise SystemExit(rc)


@cli.command()
def health() -> None:
    """Show a quick health summary of the local agent setup."""
    from rich.console import Console

    from app.cli.health_view import render_health_report
    from app.config import get_environment
    from app.integrations.store import STORE_PATH
    from app.integrations.verify import verify_integrations

    capture_cli_invoked()
    results = verify_integrations()
    render_health_report(
        console=Console(highlight=False),
        environment=get_environment().value,
        integration_store_path=STORE_PATH,
        results=results,
    )
    if any(result.get("status") in {"missing", "failed"} for result in results):
        raise SystemExit(1)


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
@click.option("--interactive", is_flag=True, help="Paste an alert JSON payload into the terminal.")
@click.option(
    "--print-template",
    type=click.Choice(["generic", "datadog", "grafana", "honeycomb", "coralogix"]),
    default=None,
    help="Print a starter alert JSON template and exit.",
)
@click.option(
    "--output", "-o", default=None, type=click.Path(), help="Output JSON file (default: stdout)."
)
def investigate(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    print_template: str | None,
    output: str | None,
) -> None:
    """Run an RCA investigation against an alert payload."""
    from app.main import main as investigate_main

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

    capture_investigation_started(
        input_path=input_path,
        input_json=input_json,
        interactive=interactive,
    )
    try:
        exit_code = investigate_main(argv)
    except Exception:
        capture_investigation_failed()
        raise
    if exit_code == 0:
        capture_investigation_completed()
    else:
        capture_investigation_failed()
    raise SystemExit(exit_code)


@cli.group()
def integrations() -> None:
    """Manage local integration credentials."""


@integrations.command()
@click.argument("service", required=False, default=None, type=click.Choice(_SETUP_SERVICES))
def setup(service: str | None) -> None:
    """Set up credentials for a service."""
    from app.integrations.cli import cmd_setup

    capture_integration_setup_started(service or "prompt")
    cmd_setup(service)
    capture_integration_setup_completed(service or "prompt")


@integrations.command(name="list")
def list_cmd() -> None:
    """List all configured integrations."""
    from app.integrations.cli import cmd_list

    capture_integrations_listed()
    cmd_list()


@integrations.command()
@click.argument("service", type=click.Choice(_SETUP_SERVICES))
def show(service: str) -> None:
    """Show details for a configured integration."""
    from app.integrations.cli import cmd_show

    cmd_show(service)


@integrations.command()
@click.argument("service", type=click.Choice(_SETUP_SERVICES))
def remove(service: str) -> None:
    """Remove a configured integration."""
    from app.integrations.cli import cmd_remove

    cmd_remove(service)
    capture_integration_removed(service)


@integrations.command()
@click.argument("service", required=False, default=None, type=click.Choice(_VERIFY_SERVICES))
@click.option(
    "--send-slack-test", is_flag=True, help="Send a test message to the configured Slack webhook."
)
def verify(service: str | None, send_slack_test: bool) -> None:
    """Verify integration connectivity (all services, or a specific one)."""
    from app.integrations.cli import cmd_verify

    cmd_verify(service, send_slack_test=send_slack_test)
    capture_integration_verified(service or "all")


@cli.group(invoke_without_command=True)
@click.pass_context
def tests(ctx: click.Context) -> None:
    """Browse and run inventoried tests from the terminal."""
    if ctx.invoked_subcommand is not None:
        return

    from app.cli.tests.discover import load_test_catalog
    from app.cli.tests.interactive import run_interactive_picker

    capture_tests_picker_opened()
    raise SystemExit(run_interactive_picker(load_test_catalog()))


@tests.command(name="synthetic")
@click.option(
    "--scenario", default="", help="Pin to a single scenario directory, e.g. 001-replication-lag."
)
@click.option("--json", "output_json", is_flag=True, help="Print machine-readable JSON results.")
@click.option(
    "--mock-grafana",
    is_flag=True,
    default=True,
    show_default=True,
    help="Serve fixture data via FixtureGrafanaBackend instead of real Grafana calls.",
)
def test_rds_synthetic(scenario: str, output_json: bool, mock_grafana: bool) -> None:
    """Run the synthetic RDS PostgreSQL RCA benchmark."""
    argv: list[str] = []
    if scenario:
        argv.extend(["--scenario", scenario])
    if output_json:
        argv.append("--json")
    if mock_grafana:
        argv.append("--mock-grafana")

    capture_test_synthetic_started(scenario or "all", mock_grafana=mock_grafana)

    from tests.synthetic.rds_postgres.run_suite import main as run_suite_main

    raise SystemExit(run_suite_main(argv))


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

    capture_tests_listed(category, search=bool(search))

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
        raise click.ClickException(
            f"Unknown test id: {test_id}. Run 'opensre tests list' to see available test ids."
        )

    capture_test_run_started(test_id, dry_run=dry_run)
    raise SystemExit(run_catalog_item(item, dry_run=dry_run))


@cli.group(invoke_without_command=True)
@click.pass_context
def deploy(ctx: click.Context) -> None:
    """Deploy OpenSRE to a cloud environment."""
    if ctx.invoked_subcommand is None:
        click.echo("Available deployment targets:\n")
        click.echo("  opensre deploy ec2          Deploy investigation server on AWS EC2 (Bedrock)")
        click.echo("  opensre deploy ec2 --down   Tear down the EC2 deployment")
        click.echo("\nRun 'opensre deploy <target> --help' for details.")


@deploy.command()
@click.option("--down", is_flag=True, default=False, help="Tear down the deployment instead of creating it.")
@click.option(
    "--branch", default="main",
    help="Git branch to clone on the instance.",
)
def ec2(down: bool, branch: str) -> None:
    """Deploy the investigation server on an AWS EC2 instance.

    \b
    Uses Amazon Bedrock for LLM inference (no API key needed).
    The instance gets an IAM role with Bedrock access.

    \b
    Examples:
      opensre deploy ec2                 # spin up the server
      opensre deploy ec2 --down          # tear it down
      opensre deploy ec2 --branch main   # deploy from a specific branch
    """
    if down:
        from tests.deployment.ec2.infrastructure_sdk.destroy_remote import destroy

        destroy()
    else:
        from tests.deployment.ec2.infrastructure_sdk.deploy_remote import deploy as run_deploy

        outputs = run_deploy(branch=branch)

        ip = outputs.get("PublicIpAddress", "")
        port = outputs.get("ServerPort", "8080")
        if ip:
            from app.cli.wizard.store import save_remote_url

            url = f"http://{ip}:{port}"
            save_remote_url(url)
            click.echo("\n  Remote URL saved. You can now run:\n    opensre remote health")


def _run_remote_interactive(ctx: click.Context) -> None:
    """Interactive menu for opensre remote when no subcommand is given."""
    import questionary
    from rich.console import Console

    from app.cli.wizard.store import load_remote_url

    console = Console(highlight=False)
    url = ctx.obj.get("url") or load_remote_url()
    status = f"  connected to {url}" if url else "  no remote URL configured"

    console.print()
    console.print(f"  [bold cyan]Remote Agent[/bold cyan]  {status}")
    console.print()

    actions = [
        questionary.Choice("Check health", value="health"),
        questionary.Choice("Run investigation (custom alert)", value="investigate"),
        questionary.Choice("Run investigation (sample alert)", value="investigate-sample"),
        questionary.Choice("List investigations", value="list"),
        questionary.Choice("Pull investigation reports", value="pull"),
        questionary.Choice("Configure remote URL", value="configure"),
        questionary.Separator(),
        questionary.Choice("Exit", value="exit"),
    ]

    action = questionary.select(
        "What would you like to do?",
        choices=actions,
        style=questionary.Style([
            ("qmark", "fg:cyan bold"),
            ("question", "bold"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
        ]),
    ).ask()

    if action is None or action == "exit":
        return

    if action == "configure":
        new_url = questionary.text(
            "Remote URL:",
            default=url or "",
            style=questionary.Style([("answer", "fg:cyan bold")]),
        ).ask()
        if new_url:
            from app.cli.wizard.store import save_remote_url

            save_remote_url(new_url)
            click.echo(f"  Saved: {new_url}")
        return

    if action == "health":
        ctx.invoke(remote_health)
    elif action == "investigate":
        alert_input = questionary.text(
            "Alert JSON payload:",
            style=questionary.Style([("answer", "fg:cyan bold")]),
        ).ask()
        if alert_input:
            ctx.invoke(remote_investigate, alert_json=alert_input)
        else:
            click.echo("  No payload provided.")
    elif action == "investigate-sample":
        from app.remote.client import SYNTHETIC_ALERT

        sample = json.dumps({
            "alert_name": "etl-daily-orders-failure",
            "pipeline_name": "etl_daily_orders",
            "severity": "critical",
            "message": SYNTHETIC_ALERT,
        })
        click.echo("  Using sample alert: etl-daily-orders-failure (critical)")
        ctx.invoke(remote_investigate, alert_json=sample)
    elif action == "list":
        ctx.invoke(remote_pull, latest=False, pull_all=False, output_dir="./investigations")
    elif action == "pull":
        mode = questionary.select(
            "Which investigations?",
            choices=[
                questionary.Choice("Latest only", value="latest"),
                questionary.Choice("All", value="all"),
            ],
            style=questionary.Style([
                ("qmark", "fg:cyan bold"),
                ("question", "bold"),
                ("pointer", "fg:cyan bold"),
                ("highlighted", "fg:cyan bold"),
            ]),
        ).ask()
        if mode == "latest":
            ctx.invoke(remote_pull, latest=True, pull_all=False, output_dir="./investigations")
        elif mode == "all":
            ctx.invoke(remote_pull, latest=False, pull_all=True, output_dir="./investigations")


@cli.group(invoke_without_command=True)
@click.option("--url", default=None, help="Remote agent base URL (e.g. 1.2.3.4 or http://host:2024).")
@click.option("--api-key", default=None, envvar="OPENSRE_API_KEY", help="API key for the remote agent.")
@click.pass_context
def remote(ctx: click.Context, url: str | None, api_key: str | None) -> None:
    """Connect to and trigger a remote deployed agent."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key

    if ctx.invoked_subcommand is None:
        _run_remote_interactive(ctx)


@remote.command(name="health")
@click.pass_context
def remote_health(ctx: click.Context) -> None:
    """Check the health of a remote deployed agent."""
    import httpx

    from app.cli.wizard.store import load_remote_url, save_remote_url
    from app.remote.client import RemoteAgentClient

    url = ctx.obj.get("url")
    api_key = ctx.obj.get("api_key")

    resolved_url = url or load_remote_url()
    if not resolved_url:
        raise click.ClickException("No remote URL configured. Pass a URL or run 'opensre remote health <url>'.")

    client = RemoteAgentClient(resolved_url, api_key=api_key)

    try:
        result = client.health()
        save_remote_url(client.base_url)
        click.echo(json.dumps(result, indent=2))
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out reaching {client.base_url}.") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Health check failed: {exc}") from exc


@remote.command(name="trigger")
@click.option("--alert-json", default=None, help="Inline alert JSON payload string.")
@click.pass_context
def remote_trigger(ctx: click.Context, alert_json: str | None) -> None:
    """Trigger an investigation on a remote deployed agent and stream results."""
    import httpx

    from app.cli.wizard.store import load_remote_url, save_remote_url
    from app.remote.client import RemoteAgentClient
    from app.remote.renderer import StreamRenderer

    url = ctx.obj.get("url")
    api_key = ctx.obj.get("api_key")

    resolved_url = url or load_remote_url()
    if not resolved_url:
        raise click.ClickException("No remote URL configured. Pass a URL or run 'opensre remote trigger <url>'.")

    alert_payload: dict | None = None
    if alert_json:
        try:
            alert_payload = json.loads(alert_json)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"Invalid alert JSON: {exc}") from exc

    client = RemoteAgentClient(resolved_url, api_key=api_key)

    try:
        events = client.trigger_investigation(alert_payload)
        renderer = StreamRenderer()
        renderer.render_stream(events)
        save_remote_url(client.base_url)
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out reaching {client.base_url}.") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Remote investigation failed: {exc}") from exc


@remote.command(name="investigate")
@click.option("--alert-json", default=None, help="Inline alert JSON payload string.")
@click.option("--sample", is_flag=True, default=False, help="Use the built-in sample alert payload.")
@click.pass_context
def remote_investigate(ctx: click.Context, alert_json: str | None, sample: bool) -> None:
    """Run an investigation on the lightweight remote server."""
    import httpx

    from app.cli.wizard.store import load_remote_url, save_remote_url
    from app.remote.client import RemoteAgentClient

    url = ctx.obj.get("url")
    api_key = ctx.obj.get("api_key")

    resolved_url = url or load_remote_url()
    if not resolved_url:
        raise click.ClickException("No remote URL configured. Pass --url or run 'opensre remote health <url>'.")

    raw_alert: dict
    if alert_json:
        try:
            raw_alert = json.loads(alert_json)
        except json.JSONDecodeError as exc:
            raise click.ClickException(f"Invalid alert JSON: {exc}") from exc
    elif sample:
        from app.remote.client import SYNTHETIC_ALERT

        raw_alert = {
            "alert_name": "etl-daily-orders-failure",
            "pipeline_name": "etl_daily_orders",
            "severity": "critical",
            "message": SYNTHETIC_ALERT,
        }
        click.echo("  Using sample alert: etl-daily-orders-failure (critical)")
    else:
        raise click.ClickException("Provide --alert-json or --sample.")

    client = RemoteAgentClient(resolved_url, api_key=api_key)

    click.echo("Sending investigation request (this may take a few minutes)...")
    try:
        result = client.investigate(raw_alert)
        save_remote_url(client.base_url)
        click.echo(f"\n  Investigation ID: {result.get('id', 'N/A')}")
        root_cause = result.get("root_cause", "")
        if root_cause:
            click.echo(f"\n  Root Cause:\n  {root_cause}")
        report = result.get("report", "")
        if report:
            click.echo(f"\n  Report:\n  {report}")
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Remote investigation failed: {exc}") from exc


@remote.command(name="pull")
@click.option("--latest", is_flag=True, default=False, help="Download only the most recent investigation.")
@click.option("--all", "pull_all", is_flag=True, default=False, help="Download all investigations.")
@click.option("--output-dir", default="./investigations", help="Directory to save .md files to.")
@click.pass_context
def remote_pull(ctx: click.Context, latest: bool, pull_all: bool, output_dir: str) -> None:
    """Download investigation .md files from the remote server."""
    import httpx

    from app.cli.wizard.store import load_remote_url, save_remote_url
    from app.remote.client import RemoteAgentClient

    url = ctx.obj.get("url")
    api_key = ctx.obj.get("api_key")

    resolved_url = url or load_remote_url()
    if not resolved_url:
        raise click.ClickException("No remote URL configured. Pass --url or run 'opensre remote health <url>'.")

    client = RemoteAgentClient(resolved_url, api_key=api_key)

    try:
        investigations = client.list_investigations()
        save_remote_url(client.base_url)
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Failed to list investigations: {exc}") from exc

    if not investigations:
        click.echo("No investigations found on the remote server.")
        return

    if not latest and not pull_all:
        click.echo(f"Found {len(investigations)} investigation(s):\n")
        for inv in investigations:
            click.echo(f"  {inv['id']}  ({inv.get('created_at', '?')})")
        click.echo("\nUse --latest or --all to download, or run:\n  opensre remote pull --latest")
        return

    from pathlib import Path

    to_download = [investigations[0]] if latest else investigations
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for inv in to_download:
        inv_id = inv["id"]
        try:
            content = client.get_investigation(inv_id)
            dest = out / f"{inv_id}.md"
            dest.write_text(content, encoding="utf-8")
            click.echo(f"  Downloaded: {dest}")
        except Exception as exc:  # noqa: BLE001
            click.echo(f"  Failed to download {inv_id}: {exc}", err=True)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``opensre`` console script."""
    load_dotenv(override=False)
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
