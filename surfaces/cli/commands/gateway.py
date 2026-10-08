"""OpenSRE gateway daemon commands (web app, Telegram chat, task scheduler)."""

from __future__ import annotations

import time

import click

from gateway.core.process import (
    GATEWAY_LOG_FILE,
    gateway_daemon_pid,
    read_component_status,
    read_gateway_log_tail,
    start_gateway_daemon,
    stop_gateway_daemon,
)
from surfaces.cli.host import cli_host
from surfaces.shared.gateway_entrypoint import gateway_entry_argv


def _echo_components() -> None:
    for name, detail in read_component_status().items():
        click.echo(f"  {name}: {detail}")


@click.group(name="gateway")
def gateway_command() -> None:
    """Run the OpenSRE gateway daemon (web app, Telegram chat, task scheduler)."""


@gateway_command.command("start")
@click.option(
    "--foreground",
    "-f",
    is_flag=True,
    help="Run attached to this terminal instead of as a background daemon.",
)
@click.pass_context
def gateway_start_command(ctx: click.Context, foreground: bool) -> None:
    """Start the gateway daemon (background by default)."""
    if foreground:
        start_gateway_foreground = cli_host(ctx).start_gateway_foreground
        if start_gateway_foreground is None:
            raise click.ClickException(
                "Foreground mode needs the opensre entrypoint; run `opensre gateway start --foreground`."
            )
        click.echo("Starting OpenSRE gateway (foreground)")
        start_gateway_foreground()
        return

    ok, message = start_gateway_daemon(argv=gateway_entry_argv())
    click.echo(message)
    click.echo(f"Logs: {GATEWAY_LOG_FILE}")
    if not ok:
        raise SystemExit(1)
    _echo_components()
    click.echo("Stop: opensre gateway stop · Status: opensre gateway status")


@gateway_command.command("web")
def gateway_web_command() -> None:
    """Serve the health and alert HTTP app in the foreground."""
    from surfaces.gateway_entry import serve_web

    serve_web()


@gateway_command.command("stop")
def gateway_stop_command() -> None:
    """Stop the background gateway daemon."""
    ok, message = stop_gateway_daemon()
    click.echo(message)
    if not ok:
        raise SystemExit(1)


@gateway_command.command("status")
def gateway_status_command() -> None:
    """Show the gateway daemon and its components (web, telegram, scheduler)."""
    pid = gateway_daemon_pid()
    click.echo(f"OpenSRE gateway: {f'running (pid {pid})' if pid else 'stopped'}")
    _echo_components()
    click.echo(f"Logs: {GATEWAY_LOG_FILE}")


@gateway_command.command("logs")
@click.option("-n", "--lines", default=50, show_default=True, help="Lines of history to print.")
@click.option("-f", "--follow", is_flag=True, help="Keep printing new log lines (Ctrl-C to exit).")
def gateway_logs_command(lines: int, follow: bool) -> None:
    """Print the gateway daemon logs."""
    if not GATEWAY_LOG_FILE.exists():
        click.echo(f"No gateway logs yet at {GATEWAY_LOG_FILE}")
        return
    if tail := read_gateway_log_tail(lines):
        click.echo(tail)
    if not follow:
        return
    try:
        with GATEWAY_LOG_FILE.open("r", errors="replace") as log:
            log.seek(0, 2)
            while True:
                if line := log.readline():
                    click.echo(line, nl=False)
                else:
                    time.sleep(0.5)
    except (KeyboardInterrupt, OSError):
        pass
