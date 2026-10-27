"""Provider setup coordination without activation or vendor-specific dispatch."""

from __future__ import annotations

import os
from collections.abc import Callable

from config.env_file import sync_env_values
from config.llm_auth.credentials import verify
from config.llm_auth.model_catalog import save_model_catalog
from config.llm_auth.records import save_provider_auth_record
from surfaces.shared.llm_setup.auth_profiles import ProviderAuthProfile, provider_for_profile
from surfaces.shared.llm_setup.auth_results import AuthSetupResult
from surfaces.shared.llm_setup.auth_service import configure_api_key_provider
from surfaces.shared.llm_setup.catalog import WizardCredentialKind
from surfaces.shared.llm_setup.discovery import discover_models
from surfaces.shared.llm_setup.persist import AuthSetupError
from surfaces.shared.llm_setup.subscription_service import configure_subscription


def connect_provider(
    *,
    profile: ProviderAuthProfile,
    model: str | None = None,
    api_key: str | None = None,
    validate: bool = True,
    settings: dict[str, str] | None = None,
    present: Callable[[str], None] = lambda _: None,
    allow_authorization: bool = True,
    profile_id: str | None = None,
    new_account: bool = False,
) -> AuthSetupResult:
    """Persist a connection; the caller decides whether to offer model selection."""
    provider = provider_for_profile(profile)
    if provider.credential_kind == WizardCredentialKind.SUBSCRIPTION:
        return configure_subscription(
            profile=profile,
            model=model,
            set_provider=False,
            present=present,
            profile_id=profile_id,
            new_account=new_account,
            credential=api_key,
            allow_authorization=allow_authorization,
        )
    previous = {key: os.environ.get(key) for key in settings or {}}
    os.environ.update(settings or {})
    try:
        if provider.credential_kind == WizardCredentialKind.API_KEY:
            if not api_key:
                raise AuthSetupError(
                    f"Pass --api-key or run opensre auth login {provider.value} in a terminal."
                )
            result = configure_api_key_provider(
                profile=profile, api_key=api_key, model=model, set_provider=False, validate=validate
            )
        else:
            if provider.credential_kind == WizardCredentialKind.CLI:
                if not provider.adapter_factory:
                    raise AuthSetupError("No CLI adapter registered.")
                adapter = provider.adapter_factory()
                probe = adapter.detect()
                if not probe.installed or probe.logged_in is False:
                    raise AuthSetupError(
                        f"{probe.detail} {adapter.install_hint} {adapter.auth_hint}"
                    )
                detail = (
                    probe.detail
                    if probe.logged_in is True
                    else "Auth checked on use. " + probe.detail
                )
                verified = probe.logged_in is True
            elif provider.credential_kind == WizardCredentialKind.NONE:
                status = verify(provider.value)
                if not status.configured:
                    raise AuthSetupError(status.detail)
                detail, verified = status.detail, status.verified
            else:
                detail, verified = "Local runtime connected.", True
            save_provider_auth_record(
                provider=provider.value,
                auth_name=profile.name,
                kind=profile.kind,
                source=profile.kind,
                detail=detail,
                verified=verified,
            )
            result = AuthSetupResult(
                provider.value, model or provider.default_model, profile.kind, detail, None
            )
        catalog = discover_models(provider)
        if catalog:
            save_model_catalog(provider.value, catalog)
        elif model:
            # Keep the validation model selectable without changing an active model env.
            catalog = ((model, model),)
            save_model_catalog(provider.value, catalog)
        if settings:
            sync_env_values(settings)
        return AuthSetupResult(
            result.provider,
            result.model,
            result.source,
            result.detail,
            result.env_path,
            catalog or (),
        )
    except BaseException:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        raise
