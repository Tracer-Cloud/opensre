"""Method and provider submenus shared by CLI and REPL login."""

from __future__ import annotations

from dataclasses import dataclass

from surfaces.shared.llm_setup.auth_profiles import (
    AuthKind,
    ProviderAuthProfile,
    iter_auth_profiles,
)
from surfaces.shared.terminal.components.subcommand_menu import repl_choose_subcommand


@dataclass(frozen=True)
class _ConnectionMethod:
    label: str
    kinds: tuple[AuthKind, ...]
    usage: str


_METHODS = (
    _ConnectionMethod("Subscription", ("subscription",), "Use your provider plan"),
    _ConnectionMethod("API key", ("api_key",), "Provider API billing"),
    _ConnectionMethod("CLI-managed", ("cli",), "Vendor CLI auth; billing may vary"),
    _ConnectionMethod("Local / cloud", ("host", "none"), "Local host or cloud IAM / ADC"),
)


def configured_profile_name() -> str | None:
    """The auth-profile name matching the install's active LLM provider, if any."""
    from config.llm_settings import get_configured_llm_provider

    configured = get_configured_llm_provider()
    for profile in iter_auth_profiles():
        if configured == profile.provider_value or configured in profile.all_names:
            return profile.name
    return None


def prompt_auth_provider() -> ProviderAuthProfile | None:
    """Choose a method and provider in subcommand trays; Esc from providers goes back."""
    profiles = iter_auth_profiles()
    configured = configured_profile_name()
    current = next((profile for profile in profiles if profile.name == configured), None)
    methods = {
        method.label: method
        for method in _METHODS
        if any(profile.kind in method.kinds for profile in profiles)
    }
    if not methods:
        return None
    initial = next(
        (method.label for method in methods.values() if current and current.kind in method.kinds),
        next(iter(methods)),
    )
    while True:
        selected = repl_choose_subcommand(
            parent="/login",
            options=tuple((method.label, method.usage) for method in methods.values()),
            initial_value=initial,
        )
        if selected is None:
            return None
        initial = selected
        method = methods[selected]
        offered = {profile.label: profile for profile in profiles if profile.kind in method.kinds}
        chosen = repl_choose_subcommand(
            parent=f"/login {method.label}",
            options=tuple(
                (profile.label, profile.auth_hint or method.usage) for profile in offered.values()
            ),
            initial_value=current.label if current and current.kind in method.kinds else None,
        )
        if chosen is not None:
            return offered[chosen]
