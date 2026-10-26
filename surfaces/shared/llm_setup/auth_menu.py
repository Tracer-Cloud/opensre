"""Shared terminal selection for provider login."""

from __future__ import annotations

from surfaces.shared.llm_setup.auth_profiles import (
    AuthKind,
    ProviderAuthProfile,
    iter_auth_profiles,
    resolve_auth_profile,
)
from surfaces.shared.terminal.components.choice_menu import repl_choose_one


def configured_profile_name() -> str | None:
    """The auth-profile name matching the install's active LLM provider, if any."""
    from config.llm_settings import get_configured_llm_provider

    configured = get_configured_llm_provider()
    for profile in iter_auth_profiles():
        if configured == profile.provider_value or configured in profile.all_names:
            return profile.name
    return None


def prompt_auth_provider() -> ProviderAuthProfile | None:
    """Choose a login provider without resolving credentials; return None on cancel."""
    configured = configured_profile_name()
    current = resolve_auth_profile(configured) if configured else None
    kind = repl_choose_one(
        title="Sign in with",
        breadcrumb="/login",
        choices=[
            ("api-keys", "API key"),
            ("subscriptions", "Subscriptions"),
        ],
        initial_value="subscriptions" if current and current.kind == "subscription" else "api-keys",
    )
    if kind is None:
        return None
    credential_kind: AuthKind = "subscription" if kind == "subscriptions" else "api_key"
    provider = repl_choose_one(
        title="Choose a provider",
        breadcrumb="/login › " + ("Subscriptions" if kind == "subscriptions" else "API key"),
        choices=[(profile.name, profile.label) for profile in iter_auth_profiles(credential_kind)],
        initial_value=configured,
    )
    if provider is None:
        return None
    return resolve_auth_profile(provider)
