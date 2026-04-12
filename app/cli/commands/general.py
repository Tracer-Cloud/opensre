"""Single-command CLI entrypoints that do not need their own groups."""

from __future__ import annotations

import json
import platform
import time

import click

from app.analytics.cli import (
    capture_cli_invoked,
    capture_investigation_completed,
    capture_investigation_failed,
    capture_investigation_started,
)
from app.cli.constants import ALERT_TEMPLATE_CHOICES
from app.cli.context import is_json_output, is_yes
from app.cli.exit_codes import ERROR, SUCCESS
from app.version import get_version


def _build_investigate_argv(
    *,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    print_template: str | None,
    output: str | None,
) -> list[str]:
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
    return argv


@click.command(name="update")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report whether an update is available without installing.",
)
@click.option("--yes", "-y", "local_yes", is_flag=True, help="Skip the confirmation prompt.")
def update_command(check_only: bool, local_yes: bool) -> None:
    """Check for a newer version and update if one is available."""
    from app.cli.update import run_update

    capture_cli_invoked()
    raise SystemExit(run_update(check_only=check_only, yes=local_yes or is_yes()))


@click.command(name="version")
def version_command() -> None:
    """Print detailed version, Python and OS info."""
    capture_cli_invoked()
    if is_json_output():
        click.echo(
            json.dumps(
                {
                    "opensre": get_version(),
                    "python": platform.python_version(),
                    "os": platform.system().lower(),
                    "arch": platform.machine(),
                }
            )
        )
        return
    click.echo(f"opensre {get_version()}")
    click.echo(f"Python  {platform.python_version()}")
    click.echo(f"OS      {platform.system().lower()} ({platform.machine()})")


@click.command(name="health")
@click.option("--watch", is_flag=True, help="Continuously refresh the health report.")
@click.option(
    "--rate", default=5, show_default=True, help="Refresh interval in seconds (with --watch)."
)
def health_command(watch: bool, rate: int) -> None:
    """Show a quick health summary of the local agent setup."""
    from app.cli.health_view import render_health_json, render_health_report
    from app.config import get_environment
    from app.integrations.store import STORE_PATH
    from app.integrations.verify import verify_integrations

    capture_cli_invoked()

    def _run_once() -> int:
        results = verify_integrations()
        environment = get_environment().value

        if is_json_output():
            render_health_json(
                environment=environment,
                integration_store_path=STORE_PATH,
                results=results,
            )
        else:
            from rich.console import Console

            render_health_report(
                console=Console(highlight=False),
                environment=environment,
                integration_store_path=STORE_PATH,
                results=results,
            )

        if any(result.get("status") in {"missing", "failed"} for result in results):
            return ERROR
        return SUCCESS

    if not watch:
        raise SystemExit(_run_once())

    try:
        while True:
            click.clear()
            _run_once()
            time.sleep(rate)
    except KeyboardInterrupt:
        raise SystemExit(SUCCESS) from None


@click.command(name="investigate")
@click.option(
    "--input",
    "-i",
    "input_path",
    default=None,
    type=click.Path(),
    help="Path to an alert file (.json, .md, .txt, ...). Use '-' to read from stdin.",
)
@click.option("--input-json", default=None, help="Inline alert JSON string.")
@click.option("--interactive", is_flag=True, help="Paste an alert JSON payload into the terminal.")
@click.option(
    "--print-template",
    type=click.Choice(ALERT_TEMPLATE_CHOICES),
    default=None,
    help="Print a starter alert JSON template and exit.",
)
@click.option(
    "--output", "-o", default=None, type=click.Path(), help="Output JSON file (default: stdout)."
)
def investigate_command(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    print_template: str | None,
    output: str | None,
) -> None:
    """Run an RCA investigation against an alert payload."""
    from app.main import main as investigate_main

    capture_investigation_started(
        input_path=input_path,
        input_json=input_json,
        interactive=interactive,
    )
    try:
        exit_code = investigate_main(
            _build_investigate_argv(
                input_path=input_path,
                input_json=input_json,
                interactive=interactive,
                print_template=print_template,
                output=output,
            )
        )
    except Exception:
        capture_investigation_failed()
        raise

    if exit_code == SUCCESS:
        capture_investigation_completed()
    else:
        capture_investigation_failed()
    raise SystemExit(exit_code)
