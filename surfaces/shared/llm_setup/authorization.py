"""Subscription authorization and account model selection presentation."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable

import click

from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE


def present_authorization(*, open_browser: bool, provider: str) -> Callable[[str], None]:
    """Display a token-free authorization URL, optionally opening the system browser."""

    spec = SUBSCRIPTION_BY_VALUE[provider]

    def present(url: str) -> None:
        click.echo(f"Continue with {spec.label}. Authorize OpenSRE; plan limits apply.")
        click.echo(f"Sign in: {url}")
        click.echo("Waiting for authorization (Ctrl+C to cancel)…")
        if open_browser:
            try:
                webbrowser.open(url)
            except (webbrowser.Error, OSError):
                click.echo("Open the sign-in URL manually to continue.")

    return present


def present_setup_page(*, label: str, url: str, open_browser: bool) -> None:
    """Offer the provider-owned key setup URL on an intentional terminal login."""
    click.echo(f"Setup page: {url}")
    if open_browser and click.confirm(f"Open {label} setup page in your browser?", default=True):
        opened = False
        try:
            opened = bool(webbrowser.open(url))
        finally:
            from infrastructure.analytics.capture import capture_browser_open_requested

            capture_browser_open_requested(target="provider_setup", opened=opened)
