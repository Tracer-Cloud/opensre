"""Browser subscription setup within the onboarding wizard."""

from __future__ import annotations

import click

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
            present=present_authorization(open_browser=True),
            choose_model=choose_subscription_model,
        )
    except (AuthSetupError, click.Abort) as exc:
        click.echo(str(exc) or "Subscription setup cancelled.")
        return None
    return result.model
