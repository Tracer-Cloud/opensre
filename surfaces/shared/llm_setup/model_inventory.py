"""Prompt-safe model inventory, with no account discovery during redraws."""

from __future__ import annotations

import os

from config.account import load_account_record
from config.constants.account import (
    OPENSRE_ACCOUNT_LLM_MODEL_ENV,
    OPENSRE_ACCOUNT_TOKEN_ENV,
    OPENSRE_GATEWAY_LLM_MODEL_DEFAULT,
)
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.llm import HOSTED_MODEL_SOURCE_ID as HOSTED_SOURCE
from config.constants.llm import LLM_PROVIDER_ENV, OPENSRE_LLM_CONNECTION_ENV
from config.llm_auth.cli_auth import CliAuthState, resolve_cli_auth_state
from config.llm_auth.credentials import status as credential_status
from config.llm_auth.model_catalog import cached_model_catalog
from config.llm_auth.records import resolve_provider_auth_record
from surfaces.shared.llm_setup.catalog import (
    SUPPORTED_PROVIDERS,
    ProviderOption,
    WizardCredentialKind,
)
from surfaces.shared.llm_setup.discovery import uses_discovery
from surfaces.shared.llm_setup.model_results import Availability, ModelChoice
from surfaces.shared.llm_setup.provider_sources import source_label


def saved_model(provider: ProviderOption) -> str:
    return os.getenv(provider.model_env, "").strip() or (
        os.getenv(provider.legacy_model_env, "").strip() if provider.legacy_model_env else ""
    )


def hosted_choice() -> ModelChoice | None:
    record = load_account_record()
    if record:
        model = record.llm_model
    elif os.getenv(OPENSRE_ACCOUNT_TOKEN_ENV) and os.getenv(WEBAPP_URL_ENV):
        model = os.getenv(OPENSRE_ACCOUNT_LLM_MODEL_ENV) or OPENSRE_GATEWAY_LLM_MODEL_DEFAULT
    else:
        return None
    return ModelChoice(
        HOSTED_SOURCE,
        model,
        model,
        "OpenSRE · Hosted credits",
        os.getenv(OPENSRE_LLM_CONNECTION_ENV, "").lower() != "local",
    )


def _provider_state(provider: ProviderOption, active: bool) -> tuple[Availability, str] | None:
    """Unknown CLI auth stays usable; definite sign-out never becomes verified."""
    record = resolve_provider_auth_record(provider.value)
    connected_hint = record.get("source") not in {None, "", "none"}
    if provider.credential_kind == WizardCredentialKind.CLI:
        cli_state = resolve_cli_auth_state(provider.value)
        if cli_state is None and provider.adapter_factory:
            probe = provider.adapter_factory().detect()
            cli_state = CliAuthState(probe.installed, probe.logged_in, probe.detail)
        if cli_state and cli_state.installed and cli_state.logged_in is True:
            return "available", ""
        if cli_state and cli_state.installed and cli_state.logged_in is None:
            return "auth-on-use", "Auth checked on use"
        return (
            ("reconnect", f"Run opensre auth login {provider.value}")
            if active or connected_hint
            else None
        )
    if provider.credential_kind == WizardCredentialKind.NONE:
        # Ambient SDK discovery belongs to intentional setup, never picker redraw.
        from surfaces.shared.llm_setup.ambient_status import ambient_configured

        return (
            ("auth-on-use", "Credentials checked on use")
            if active or connected_hint or ambient_configured(provider.value)
            else None
        )
    if provider.credential_kind == WizardCredentialKind.HOST:
        return (
            ("available", "")
            if active or connected_hint or os.getenv(provider.api_key_env)
            else None
        )
    state = credential_status(provider.value)
    if state.configured and not state.stale:
        return "available", ""
    return (
        ("reconnect", f"Run opensre auth login {provider.value}")
        if active or connected_hint
        else None
    )


def model_choices(*, provider_id: str | None = None) -> tuple[ModelChoice, ...]:
    try:
        hosted = hosted_choice()
    except Exception:
        hosted = ModelChoice(
            HOSTED_SOURCE,
            "",
            "Reconnect OpenSRE",
            "OpenSRE · Hosted credits",
            availability="reconnect",
            detail="Run opensre account login, then retry.",
        )
    active_provider = (
        HOSTED_SOURCE
        if hosted and hosted.active
        else os.getenv(LLM_PROVIDER_ENV, "anthropic").strip().lower()
    )
    providers = sorted(SUPPORTED_PROVIDERS, key=lambda p: p.value != active_provider)
    result: list[ModelChoice] = (
        [hosted] if hosted and (provider_id is None or provider_id == HOSTED_SOURCE) else []
    )
    for provider in providers:
        if provider_id and provider.value != provider_id:
            continue
        active = provider.value == active_provider
        try:
            explicitly_configured = bool(
                os.getenv(LLM_PROVIDER_ENV, "").strip() or saved_model(provider)
            )
            state = _provider_state(provider, active and explicitly_configured)
            cached = cached_model_catalog(provider.value)
        except Exception:
            cached = ()
            state = (
                "reconnect",
                f"Could not check connection. Run opensre auth login {provider.value}",
            )
        if state is None:
            continue
        availability, detail = state
        discovered = (
            provider.credential_kind == WizardCredentialKind.SUBSCRIPTION
            or uses_discovery(provider)
        )
        models = list(cached if discovered else tuple((m.value, m.label) for m in provider.models))
        if not discovered:
            models.extend(
                (model, label)
                for model, label in cached
                if model not in {value for value, _ in models}
            )
        current = saved_model(provider)
        if current and current not in {m for m, _ in models}:
            models.append((current, current))
        if not models and provider.default_model and not discovered:
            models.append((provider.default_model, provider.default_model))
        if provider.credential_kind == WizardCredentialKind.CLI and "" not in {
            m for m, _ in models
        }:
            models.insert(0, ("", "CLI default"))
        if not models:
            result.append(
                ModelChoice(
                    provider.value,
                    "",
                    "Reconnect / retry discovery",
                    source_label(provider),
                    False,
                    "reconnect",
                    f"Run opensre auth login {provider.value}",
                )
            )
            continue
        for model, label in models:
            result.append(
                ModelChoice(
                    provider.value,
                    model,
                    label or model or "CLI default",
                    source_label(provider),
                    active and model == (current or provider.default_model),
                    availability,
                    detail,
                )
            )
    return tuple(sorted(result, key=lambda row: row.provider_id != active_provider))


def effective_choice() -> ModelChoice:
    from config.account import account_llm_route
    from core.llm.provider_models import resolve_provider_models
    from surfaces.shared.llm_setup.effective import load_llm_settings
    from surfaces.shared.llm_setup.selection import resolve_provider

    route = account_llm_route()
    if route is not None:
        return ModelChoice(
            HOSTED_SOURCE, route.model, route.model, "OpenSRE · Hosted credits", True
        )
    settings = load_llm_settings()
    provider = resolve_provider(settings.provider if settings is not None else None)
    model = (
        resolve_provider_models(settings, provider.value)[0]
        if settings is not None
        else saved_model(provider) or provider.default_model
    )
    try:
        state = _provider_state(provider, True)
    except Exception:
        state = None
    availability, detail = state or ("reconnect", "Run opensre auth login " + provider.value)
    return ModelChoice(
        provider.value,
        model,
        model or "CLI default",
        source_label(provider),
        True,
        availability,
        detail,
    )
