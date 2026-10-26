"""Browser subscription setup within the onboarding wizard."""

from __future__ import annotations

import click

from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE
from surfaces.cli.commands.subscription_auth import choose_subscription_model, present_authorization
from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile
from surfaces.shared.llm_setup.catalog import ProviderOption
from surfaces.shared.llm_setup.persist import AuthSetupError
from surfaces.shared.llm_setup.subscription_service import configure_subscription


def configure_subscription_wizard(provider: ProviderOption) -> str | None:
    """Complete browser authorization before the wizard persists its provider choice."""
    try:
        result = configure_subscription(
            profile=resolve_auth_profile(provider.value),
            model=None,
            set_provider=False,
            present=present_authorization(open_browser=True, provider=provider.value),
            choose_model=lambda models: choose_subscription_model(models, provider=provider.value),
            credential=click.prompt(f"{provider.label} key", hide_input=True)
            if SUBSCRIPTION_BY_VALUE[provider.value].auth_method == "api_key"
            else None,
        )
    except (AuthSetupError, click.Abort) as exc:
        click.echo(str(exc) or "Subscription setup cancelled.")
        return None
    return result.model
