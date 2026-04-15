"""Integration management CLI commands."""

from __future__ import annotations

import click

from app.analytics.cli import (
    capture_integration_removed,
    capture_integration_setup_completed,
    capture_integration_setup_started,
    capture_integration_verified,
    capture_integrations_listed,
)
from app.cli.constants import MANAGED_INTEGRATION_SERVICES, SETUP_SERVICES, VERIFY_SERVICES


@click.group(name="integrations")
def integrations() -> None:
    """Manage local integration credentials."""


@integrations.command(name="setup")
@click.argument("service", required=False, default=None, type=click.Choice(SETUP_SERVICES))
def setup_integration(service: str | None) -> None:
    """Set up credentials for a service."""
    from app.integrations.cli import cmd_setup, cmd_verify

    normalized_service = service or "prompt"
    capture_integration_setup_started(normalized_service)
    resolved_service = cmd_setup(service)
    capture_integration_setup_completed(resolved_service)

    if resolved_service in VERIFY_SERVICES:
        click.echo(f"  Verifying {resolved_service}...\n")
        exit_code = cmd_verify(resolved_service)
        capture_integration_verified(resolved_service)
        raise SystemExit(exit_code)


@integrations.command(name="list")
def list_integrations() -> None:
    """List all configured integrations."""
    from app.integrations.cli import cmd_list

    capture_integrations_listed()
    cmd_list()


@integrations.command(name="show")
@click.argument("service", type=click.Choice(MANAGED_INTEGRATION_SERVICES))
def show_integration(service: str) -> None:
    """Show details for a configured integration."""
    from app.integrations.cli import cmd_show

    cmd_show(service)


@integrations.command(name="remove")
@click.argument("service", type=click.Choice(MANAGED_INTEGRATION_SERVICES))
def remove_integration(service: str) -> None:
    """Remove a configured integration."""
    from app.integrations.cli import cmd_remove

    cmd_remove(service)
    capture_integration_removed(service)


@integrations.command(name="verify")
@click.argument("service", required=False, default=None, type=click.Choice(VERIFY_SERVICES))
@click.option(
    "--send-slack-test", is_flag=True, help="Send a test message to the configured Slack webhook."
)
def verify_integration(
    service: str | None,
    send_slack_test: bool,
) -> None:
    """Verify integration connectivity (all services, or a specific one)."""
    from app.integrations.cli import cmd_verify

    exit_code = cmd_verify(
        service,
        send_slack_test=send_slack_test,
    )
    capture_integration_verified(service or "all")
    raise SystemExit(exit_code)
