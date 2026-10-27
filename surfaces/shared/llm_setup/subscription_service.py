"""Subscription setup without exposing tokens to terminal presentation."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from config.llm_auth.model_catalog import save_model_catalog
from config.llm_auth.records import resolve_provider_auth_record, save_provider_auth_record_values
from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE
from core.llm.subscriptions.registry import subscription_adapter
from core.llm.subscriptions.types import SubscriptionLoginRequired
from surfaces.shared.llm_setup.auth_profiles import ProviderAuthProfile, provider_for_profile
from surfaces.shared.llm_setup.auth_results import AuthSetupResult, AuthStatus
from surfaces.shared.llm_setup.persist import AuthSetupError
from surfaces.shared.llm_setup.selection import select_provider


def configure_subscription(
    *,
    profile: ProviderAuthProfile,
    model: str | None,
    set_provider: bool,
    present: Callable[[str], None],
    allow_authorization: bool = True,
    profile_id: str | None = None,
    new_account: bool = False,
    credential: str | None = None,
) -> AuthSetupResult:
    """Complete authorization and model selection before selecting local billing."""
    provider = provider_for_profile(profile)
    adapter = subscription_adapter(provider.value).auth
    spec = SUBSCRIPTION_BY_VALUE[provider.value]

    catalog: list[tuple[str, str]] = []

    def select(models: list[tuple[str, str]]) -> str:
        if not models:
            raise AuthSetupError(f"No models available to this {provider.label} account.")
        catalog[:] = models
        available = {slug for slug, _ in models}
        selected = (
            model.strip()
            if model is not None
            else (provider.default_model if provider.default_model in available else models[0][0])
        )
        if selected not in {slug for slug, _ in models}:
            raise AuthSetupError(f"Choose a model available to this {provider.label} account.")
        return selected

    try:
        existing = resolve_provider_auth_record(provider.value)
        reuse = (
            not profile_id
            and not new_account
            and credential is None
            and existing.get("source") == "subscription"
        )

        def authorize() -> str:
            if not allow_authorization:
                raise AuthSetupError(
                    f"Run opensre auth login {provider.value} in a terminal, or pass --no-open-browser explicitly for remote authorization."
                )
            return adapter.login(
                present,
                profile_id=profile_id,
                new_account=new_account,
                select_model=select,
                **({"credential": credential} if credential is not None else {}),
            )

        if reuse:
            try:
                models = adapter.verify()
            except SubscriptionLoginRequired:
                selected_model = authorize()
            else:
                selected_model = select(models)
        else:
            selected_model = authorize()
        record = resolve_provider_auth_record(provider.value)
        save_provider_auth_record_values(
            provider.value,
            {
                **record,
                "kind": "subscription",
                "source": "subscription",
                "verified": "true",
                "stale": "false",
            },
        )
        save_model_catalog(provider.value, catalog)
        env_path = (
            select_provider(provider.value, selected_model).env_path if set_provider else None
        )
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
        if isinstance(exc, (SubscriptionLoginRequired, AuthSetupError)):
            raise AuthSetupError(str(exc)) from exc
        raise AuthSetupError(
            f"{provider.label} setup failed. Check connectivity and local credential storage, then retry."
        ) from exc
    return AuthSetupResult(
        provider.value,
        selected_model,
        "subscription",
        f"{provider.label} authorized. {spec.usage_hint}",
        env_path,
        choices=tuple(catalog),
    )


def verify_subscription(profile: ProviderAuthProfile) -> AuthStatus:
    """Validate current account access without opening a browser."""
    provider = profile.provider_value
    try:
        models = subscription_adapter(provider).auth.verify()
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
        detail = (
            str(exc)
            if isinstance(exc, SubscriptionLoginRequired)
            else f"{profile.label} verification failed. Check connectivity and run login again if needed."
        )
        raise AuthSetupError(detail) from exc
    detail = f"{profile.label} credentials accepted; {len(models)} models available. Inference remains subject to plan limits."
    save_model_catalog(provider, models)
    record = resolve_provider_auth_record(provider)
    save_provider_auth_record_values(provider, {**record, "verified": "true", "stale": "false"})
    return AuthStatus(provider, profile.label, True, "subscription", detail, verified=True)


def logout_subscription(provider: str) -> str:
    """Clear subscription tokens without touching API keys or OpenSRE identity."""
    try:
        result = subscription_adapter(provider).auth.logout()
        save_model_catalog(provider, ())
        return result
    except (RuntimeError, ValueError) as exc:
        raise AuthSetupError(str(exc)) from exc
