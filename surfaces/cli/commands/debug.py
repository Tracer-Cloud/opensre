"""Debugging commands for runtime diagnostics."""

from __future__ import annotations

import click

from infrastructure.observability.errors.sentry import (
    capture_exception,
    resolved_sentry_dsn_host,
    sentry_transport_enabled,
)


@click.group(name="debug")
def debug_command() -> None:
    """Run targeted debug checks."""


@debug_command.command(name="sentry")
def debug_sentry_command() -> None:
    """Send a synthetic Sentry event and flush the transport."""
    dsn_host = resolved_sentry_dsn_host()
    if not sentry_transport_enabled():
        click.echo("Sentry is disabled or no DSN is configured.", err=True)
        raise SystemExit(1)

    import sentry_sdk

    event_id = capture_exception(
        RuntimeError("OpenSRE Sentry debug smoke test"),
        context="debug.sentry",
        tags={"debug": "true", "surface": "debug"},
    )
    if event_id is None:
        click.echo("Sentry did not return an event ID.", err=True)
        raise SystemExit(1)

    try:
        flush_result = sentry_sdk.flush(timeout=5)
    except Exception as exc:
        click.echo(f"Sentry flush failed: {type(exc).__name__}: {exc}", err=True)
        raise SystemExit(1) from exc

    # sentry-sdk 2.x waits for transport flushes but returns None; older/test
    # transports may return False to signal that pending work was not flushed.
    sent = flush_result is not False
    click.echo(f"Sentry DSN host: {dsn_host or '<empty>'}")
    click.echo(f"Sentry event ID: {event_id}")
    click.echo(f"Sentry flush sent: {'yes' if sent else 'no'}")

    if not sent:
        raise SystemExit(1)
