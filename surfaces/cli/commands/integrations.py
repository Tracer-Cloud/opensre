"""Integration management CLI commands."""

from __future__ import annotations

import click

from infrastructure.analytics.capture import (
    capture_integration_removed,
    capture_integration_setup_completed,
    capture_integration_setup_started,
    capture_integration_verified,
    capture_integrations_listed,
)
from surfaces.cli import constants


class IntegrationServiceChoice(click.Choice):
    """``click.Choice`` that resolves integration-management service aliases.

    Applies ``resolve_management_service`` before the enum check so management-only
    aliases (when defined) are accepted while keeping Click's friendly
    ``[[a|b|c]]`` usage/error display and shell completion.

    The choices are read from :mod:`surfaces.cli.constants` on each access rather
    than captured when the decorator runs. A plugin registers its integration
    after this module is imported, and a list baked into the ``click.Command``
    would reject it before ``cmd_setup`` ever saw it.
    """

    def __init__(self, source: str) -> None:
        # ``click.Choice.__init__`` only assigns ``choices`` and ``case_sensitive``;
        # ``choices`` is a live property here, so set the other one directly.
        self._source = source
        self.case_sensitive = True

    @property
    def choices(self) -> tuple[str, ...]:  # type: ignore[override]
        return tuple(getattr(constants, self._source))

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> object:
        if isinstance(value, str):
            from integrations.registry import resolve_management_service

            value = resolve_management_service(value)
        return super().convert(value, param, ctx)


@click.group(name="integrations")
def integrations() -> None:
    """Manage local integration credentials."""


@integrations.command(name="setup")
@click.argument(
    "service", required=False, default=None, type=IntegrationServiceChoice("SETUP_SERVICES")
)
def setup_integration(service: str | None) -> None:
    """Connect a service with guided instructions and verify it before saving."""
    from integrations.cli import cmd_setup

    normalized_service = service or "prompt"
    capture_integration_setup_started(normalized_service)
    resolved_service = cmd_setup(service)
    capture_integration_setup_completed(resolved_service)

    if resolved_service in constants.VERIFY_SERVICES:
        capture_integration_verified(resolved_service)


@integrations.command(name="list")
def list_integrations() -> None:
    """List all configured integrations."""
    from integrations.cli import cmd_list

    capture_integrations_listed()
    cmd_list()


@integrations.command(name="show")
@click.argument("service")
def show_integration(service: str) -> None:
    """Show details for a configured integration."""
    from integrations.cli import cmd_show

    cmd_show(service)


@integrations.command(name="remove")
@click.argument("service")
def remove_integration(service: str) -> None:
    """Remove a configured integration."""
    from integrations.cli import cmd_remove

    cmd_remove(service)
    capture_integration_removed(service)


@integrations.command(name="verify")
@click.argument(
    "service", required=False, default=None, type=IntegrationServiceChoice("VERIFY_SERVICES")
)
@click.option(
    "--send-slack-test", is_flag=True, help="Send a test message to the configured Slack webhook."
)
def verify_integration(
    service: str | None,
    send_slack_test: bool,
) -> None:
    """Verify integration connectivity (all services, or a specific one)."""
    from integrations.cli import cmd_verify

    exit_code = cmd_verify(
        service,
        send_slack_test=send_slack_test,
    )
    if exit_code == 0:
        capture_integration_verified(service or "all")
    raise SystemExit(exit_code)


@integrations.command(name="sync")
def sync_integrations() -> None:
    """Pull the GitHub connection from your OpenSRE workspace onto this machine."""
    from integrations.github import describe_github_sync, sync_workspace_github

    result = sync_workspace_github()
    click.echo(describe_github_sync(result))
    if result.status in {"signed_out", "unavailable"}:
        raise SystemExit(1)
