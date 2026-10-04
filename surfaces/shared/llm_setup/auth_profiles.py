"""Provider metadata for browser-assisted LLM auth setup (API keys only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from surfaces.shared.llm_setup.catalog import (
    PROVIDER_BY_VALUE,
    SUPPORTED_PROVIDERS,
    ProviderOption,
    WizardCredentialKind,
)

AuthKind = Literal["api_key"]


@dataclass(frozen=True)
class ProviderAuthProfile:
    """User-facing auth metadata for one provider setup path."""

    name: str
    provider_value: str
    label: str
    kind: AuthKind
    aliases: tuple[str, ...] = ()
    setup_url: str | None = None
    auth_hint: str = ""

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, self.provider_value, *self.aliases)


_API_KEY_SETUP_URLS: dict[str, str] = {
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "openrouter": "https://openrouter.ai/keys",
    "trustedrouter": "https://trustedrouter.com/keys",
    "deepseek": "https://platform.deepseek.com/api_keys",
    "gemini": "https://aistudio.google.com/app/apikey",
    "nvidia": "https://build.nvidia.com/",
    "minimax": "https://intl.minimaxi.com/user-center/basic-information/interface-key",
    "groq": "https://console.groq.com/keys",
}


def _api_key_profiles() -> tuple[ProviderAuthProfile, ...]:
    profiles: list[ProviderAuthProfile] = []
    for provider in SUPPORTED_PROVIDERS:
        if provider.credential_kind != WizardCredentialKind.API_KEY:
            continue
        profiles.append(
            ProviderAuthProfile(
                name=provider.value,
                provider_value=provider.value,
                label=provider.label
                if provider.label.lower().endswith("api key")
                else f"{provider.label} API key",
                kind="api_key",
                setup_url=_API_KEY_SETUP_URLS.get(provider.value),
                auth_hint=f"Paste {provider.api_key_env}",
            )
        )
    return tuple(profiles)


def iter_auth_profiles() -> tuple[ProviderAuthProfile, ...]:
    """Return all supported auth setup paths."""
    return _api_key_profiles()


def _normalize_auth_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def resolve_auth_profile(raw_name: str) -> ProviderAuthProfile:
    """Resolve a user-supplied provider/auth alias to an auth profile.

    Case-insensitive, and ``_`` matches ``-`` (``custom_openai`` is ``custom-openai``).
    """
    normalized = _normalize_auth_name(raw_name)
    if not normalized:
        raise KeyError(raw_name)
    for profile in iter_auth_profiles():
        if normalized in {_normalize_auth_name(name) for name in profile.all_names}:
            return profile
    raise KeyError(raw_name)


def provider_for_profile(profile: ProviderAuthProfile) -> ProviderOption:
    """Return the catalog provider option for an auth profile."""
    return PROVIDER_BY_VALUE[profile.provider_value]


__all__ = [
    "AuthKind",
    "ProviderAuthProfile",
    "iter_auth_profiles",
    "provider_for_profile",
    "resolve_auth_profile",
]
