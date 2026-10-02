"""LLM auth setup operations: configure, verify, status and logout for a provider."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from config.llm_auth.credentials import (
    delete as delete_provider_auth,
)
from config.llm_auth.credentials import (
    save_api_key,
)
from config.llm_auth.credentials import (
    status as provider_auth_status,
)
from config.llm_auth.credentials import (
    verify as verify_provider_auth,
)
from config.llm_auth.records import (
    delete_provider_auth_record,
    resolve_provider_auth_record,
    save_provider_auth_record,
)
from config.secrets.backend import KeyringUnavailableError
from surfaces.shared.llm_setup.auth_profiles import (
    ProviderAuthProfile,
    provider_for_profile,
    resolve_auth_profile,
)
from surfaces.shared.llm_setup.catalog import WizardCredentialKind
from surfaces.shared.llm_setup.env_sync import sync_provider_env
from surfaces.shared.llm_setup.persist import AuthSetupError, persist_api_key_secret
from surfaces.shared.llm_setup.validation import validate_provider_credentials


@dataclass(frozen=True)
class AuthStatus:
    """Status row for one provider auth path."""

    provider: str
    label: str
    authenticated: bool
    source: str
    detail: str
    verified: bool = False
    stale: bool = False


@dataclass(frozen=True)
class AuthSetupResult:
    """Result from configuring a provider auth path."""

    provider: str
    model: str
    source: str
    detail: str
    env_path: Path | None


def _save_auth_record(
    *,
    provider_value: str,
    profile: ProviderAuthProfile,
    source: str,
    detail: str,
) -> None:
    save_provider_auth_record(
        provider=provider_value,
        auth_name=profile.name,
        kind=profile.kind,
        source=source,
        detail=detail,
    )


def configure_api_key_provider(
    *,
    profile: ProviderAuthProfile,
    api_key: str,
    model: str | None = None,
    set_provider: bool = True,
    validate: bool = True,
    env_path: Path | None = None,
) -> AuthSetupResult:
    """Validate and persist an API-key provider credential."""
    from config.account import account_llm_route

    account_route = account_llm_route()
    if account_route is not None:
        raise AuthSetupError(
            "LLM provider authentication is managed by your OpenSRE account: "
            f"openai ({account_route.model}, hosted by OpenSRE). "
            "Sign out with `/logout` or `opensre account logout` before configuring another provider."
        )

    provider = provider_for_profile(profile)
    if provider.credential_kind != WizardCredentialKind.API_KEY or not provider.api_key_env:
        raise AuthSetupError(f"{provider.label} does not use an OpenSRE-managed API key.")

    normalized_key = api_key.strip()
    if not normalized_key:
        raise AuthSetupError(f"{provider.api_key_env} cannot be empty.")

    selected_model = (model if model is not None else provider.default_model).strip()
    if validate:
        validation = validate_provider_credentials(
            provider=provider,
            api_key=normalized_key,
            model=selected_model,
        )
        if not validation.ok:
            raise AuthSetupError(validation.detail)

    try:
        # No hardcoded detail: the store reports where the credential landed
        # (the local credentials file, or the env-only fallback), and
        # save_api_key just wrote the matching metadata record.
        saved = save_api_key(provider.value, normalized_key)
    except (RuntimeError, ValueError) as exc:
        raise AuthSetupError(str(exc)) from exc

    extra_env = {provider.api_key_env: normalized_key}
    written_path = (
        sync_provider_env(
            provider=provider,
            model=selected_model,
            extra_env=extra_env,
            env_path=env_path,
        )
        if set_provider
        else None
    )
    source = "fallback" if saved.used_fallback else "local-file"
    _save_auth_record(
        provider_value=provider.value, profile=profile, source=source, detail=saved.detail
    )
    return AuthSetupResult(
        provider=provider.value,
        model=selected_model,
        source=source,
        detail=saved.detail,
        env_path=written_path,
    )


def provider_status(raw_name: str) -> AuthStatus:
    """Return auth status for an auth profile or provider alias."""
    profile = resolve_auth_profile(raw_name)
    provider = provider_for_profile(profile)
    record = resolve_provider_auth_record(provider.value)

    resolved = provider_auth_status(provider.value)
    source = resolved.source
    authenticated = resolved.configured and not resolved.stale
    detail = resolved.detail
    if record.get("detail") and authenticated:
        detail = record["detail"]
    return AuthStatus(
        provider.value,
        profile.label,
        authenticated,
        source,
        detail,
        verified=resolved.verified,
        stale=resolved.stale,
    )


def verify_provider(raw_name: str) -> AuthStatus:
    """Intentionally resolve request-time credentials and refresh metadata."""
    profile = resolve_auth_profile(raw_name)
    provider = provider_for_profile(profile)
    resolved = verify_provider_auth(provider.value)
    return AuthStatus(
        provider.value,
        profile.label,
        resolved.configured and not resolved.stale,
        resolved.source,
        resolved.detail,
        verified=resolved.verified,
        stale=resolved.stale,
    )


def logout_provider(raw_name: str) -> str:
    """Delete a provider's API key from every local tier and clear metadata."""
    from config.env_file import clear_env_value

    profile = resolve_auth_profile(raw_name)
    provider = provider_for_profile(profile)
    delete_provider_auth_record(provider.value)
    try:
        delete_provider_auth(provider.value)
    except KeyringUnavailableError as exc:
        raise AuthSetupError(str(exc)) from exc
    if provider.api_key_env:
        os.environ.pop(provider.api_key_env, None)
        clear_env_value(provider.api_key_env)
    return f"Removed {provider.api_key_env} from OpenSRE's local credential storage."


__all__ = [
    "AuthSetupError",
    "AuthSetupResult",
    "AuthStatus",
    "configure_api_key_provider",
    "logout_provider",
    "persist_api_key_secret",
    "provider_status",
    "verify_provider",
]
