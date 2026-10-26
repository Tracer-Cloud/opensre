"""Subscription setup without exposing tokens to terminal presentation."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from config.llm_auth.records import resolve_provider_auth_record, save_provider_auth_record_values
from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE
from core.llm.subscriptions.registry import subscription_adapter
from core.llm.subscriptions.types import SubscriptionLoginRequired
from surfaces.shared.llm_setup.auth_profiles import ProviderAuthProfile, provider_for_profile
from surfaces.shared.llm_setup.auth_results import AuthSetupResult, AuthStatus
from surfaces.shared.llm_setup.env_sync import sync_provider_env
from surfaces.shared.llm_setup.persist import AuthSetupError


def configure_subscription(
    *,
    profile: ProviderAuthProfile,
    model: str | None,
    set_provider: bool,
    present: Callable[[str], None],
    choose_model: Callable[[list[tuple[str, str]]], str],
    profile_id: str | None = None,
    new_account: bool = False,
    credential: str | None = None,
) -> AuthSetupResult:
    """Complete authorization and model selection before selecting local billing."""
    provider = provider_for_profile(profile)
    adapter = subscription_adapter(provider.value).auth
    spec = SUBSCRIPTION_BY_VALUE[provider.value]

    def select(models: list[tuple[str, str]]) -> str:
        selected = model.strip() if model is not None else choose_model(models)
        if selected not in {slug for slug, _ in models}:
            raise AuthSetupError(f"Choose a model available to this {provider.label} account.")
        return selected

    try:
        selected_model = adapter.login(
            present,
            profile_id=profile_id,
            new_account=new_account,
            select_model=select,
            **({"credential": credential} if credential is not None else {}),
        )
        env_path = (
            sync_provider_env(provider=provider, model=selected_model) if set_provider else None
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
    record = resolve_provider_auth_record(provider)
    save_provider_auth_record_values(provider, {**record, "verified": "true", "stale": "false"})
    return AuthStatus(provider, profile.label, True, "subscription", detail, verified=True)


def logout_subscription(provider: str) -> str:
    """Clear subscription tokens without touching API keys or OpenSRE identity."""
    try:
        return subscription_adapter(provider).auth.logout()
    except (RuntimeError, ValueError) as exc:
        raise AuthSetupError(str(exc)) from exc
