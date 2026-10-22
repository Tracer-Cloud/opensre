"""ChatGPT browser authorization and account model selection presentation."""

from __future__ import annotations

import sys
import webbrowser
from collections.abc import Callable

import click
import questionary

from infrastructure.terminal.prompt_support import QUESTIONARY_QMARK, questionary_prompt_style


def present_authorization(*, open_browser: bool) -> Callable[[str], None]:
    """Display a token-free authorization URL, optionally opening the system browser."""

    def present(url: str) -> None:
        click.echo("Continue with ChatGPT. Authorize OpenSRE in your browser; plan limits apply.")
        click.echo(f"Sign in: {url}")
        click.echo("Waiting for browser callback on this machine (Ctrl+C to cancel)…")
        if open_browser:
            try:
                webbrowser.open(url)
            except (webbrowser.Error, OSError):
                click.echo("Open the sign-in URL manually to continue.")

    return present


def choose_subscription_model(models: list[tuple[str, str]]) -> str:
    """Select only a model visible to the signed-in ChatGPT account."""
    if not sys.stdin.isatty():
        click.echo(f"Using model: {models[0][0]} (override with --model).")
        return models[0][0]
    selected = questionary.select(
        "Choose a ChatGPT model:",
        choices=[questionary.Choice(f"{label} ({slug})", value=slug) for slug, label in models],
        qmark=QUESTIONARY_QMARK,
        style=questionary_prompt_style(),
    ).ask()
    if selected is None:
        raise click.Abort()
    return str(selected)
