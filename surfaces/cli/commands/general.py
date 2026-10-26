"""Single-command CLI entrypoints that do not need their own groups."""

from __future__ import annotations

import json
import platform
import time

import click

from config.version import get_opensre_version
from infrastructure.analytics.capture import (
    capture_update_completed,
    capture_update_failed,
    capture_update_started,
)
from infrastructure.process.exit_codes import ERROR, SUCCESS
from infrastructure.process.runtime_flags import is_json_output, is_yes


@click.command(name="uninstall")
@click.option("--yes", "-y", "local_yes", is_flag=True, help="Skip the confirmation prompt.")
def uninstall_command(local_yes: bool) -> None:
    """Remove opensre and all local data from this machine."""
    from surfaces.cli.lifecycle.uninstall import run_uninstall

    raise SystemExit(run_uninstall(yes=local_yes or is_yes()))


@click.command(name="update")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report whether an update is available without installing.",
)
@click.option("--yes", "-y", "local_yes", is_flag=True, help="Skip the confirmation prompt.")
def update_command(check_only: bool, local_yes: bool) -> None:
    """Check for a newer main build and update if one is available."""
    from surfaces.cli.lifecycle.update import run_update

    capture_update_started(check_only=check_only)
    try:
        exit_code = run_update(check_only=check_only, yes=local_yes or is_yes())
    except Exception as exc:
        capture_update_failed(check_only=check_only, reason=type(exc).__name__)
        raise

    capture_update_completed(
        check_only=check_only,
        updated=exit_code == 0 and not check_only,
    )
    raise SystemExit(exit_code)


@click.command(name="version")
def version_command() -> None:
    """Print detailed version, Python and OS info."""
    if is_json_output():
        click.echo(
            json.dumps(
                {
                    "opensre": get_opensre_version(),
                    "python": platform.python_version(),
                    "os": platform.system().lower(),
                    "arch": platform.machine(),
                }
            )
        )
        return
    click.echo(f"opensre {get_opensre_version()}")
    click.echo(f"Python  {platform.python_version()}")
    click.echo(f"OS      {platform.system().lower()} ({platform.machine()})")


@click.command(name="health")
@click.option("--watch", is_flag=True, help="Continuously refresh the health report.")
@click.option(
    "--rate", default=5, show_default=True, help="Refresh interval in seconds (with --watch)."
)
def health_command(watch: bool, rate: int) -> None:
    """Show a quick health summary of the local agent setup."""
    from config.constants.paths import integrations_store_path
    from config.environment import get_environment
    from integrations.verify import verify_integrations
    from surfaces.shared.terminal.health import render_health_json, render_health_report

    def _run_once() -> int:
        results = verify_integrations()
        environment = get_environment().value

        if is_json_output():
            render_health_json(
                environment=environment,
                integration_store_path=integrations_store_path(),
                results=results,
            )
        else:
            from rich.console import Console

            render_health_report(
                console=Console(highlight=False),
                environment=environment,
                integration_store_path=integrations_store_path(),
                results=results,
            )

        if any(result.get("status") == "failed" for result in results):
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
