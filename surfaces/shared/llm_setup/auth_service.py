"""LLM auth setup operations: configure, verify, status and logout for a provider."""

from __future__ import annotations

import os
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
from surfaces.shared.llm_setup.auth_results import AuthSetupResult, AuthStatus
from surfaces.shared.llm_setup.catalog import WizardCredentialKind
from surfaces.shared.llm_setup.persist import AuthSetupError, persist_api_key_secret
from surfaces.shared.llm_setup.validation import validate_provider_credentials


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
    set_provider: bool = False,
    validate: bool = True,
    env_path: Path | None = None,
) -> AuthSetupResult:
    """Validate and persist an API-key provider credential."""
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

    # A deliberate replacement must shadow the old process export immediately.
    os.environ[provider.api_key_env] = normalized_key
    # Replace an existing .env credential too: it would otherwise shadow the new
    # stored key when the next process starts. Do not introduce a new .env secret.
    from config.env_file import env_assignment_key, read_env_lines, sync_env_values
    from config.local_env import get_project_env_path

    credential_env_path = env_path or get_project_env_path()
    if any(
        env_assignment_key(line) == provider.api_key_env
        for line in read_env_lines(credential_env_path)
    ):
        sync_env_values({provider.api_key_env: normalized_key}, env_path=credential_env_path)
    from surfaces.shared.llm_setup.selection import select_provider

    selection = (
        select_provider(provider.value, selected_model, env_path=env_path) if set_provider else None
    )
    written_path = selection.env_path if selection else None
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
        selection_changed=selection.changed if selection else False,
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
    if profile.kind == "subscription":
        from surfaces.shared.llm_setup.subscription_service import verify_subscription

        return verify_subscription(profile)
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
    if profile.kind == "subscription":
        from surfaces.shared.llm_setup.subscription_service import logout_subscription

        return logout_subscription(provider.value)
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
    "configure_api_key_provider",
    "logout_provider",
    "persist_api_key_secret",
    "provider_status",
    "verify_provider",
]
