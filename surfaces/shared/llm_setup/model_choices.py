"""Prompt-safe model choices with exact provider and billing-source identity."""

from __future__ import annotations

import os
from dataclasses import dataclass

from config.constants.llm import HOSTED_MODEL_PROVIDER, LLM_PROVIDER_ENV, OPENSRE_LLM_CONNECTION_ENV
from config.llm_auth.credentials import connection_status
from config.llm_auth.model_catalog import saved_models
from config.llm_auth.records import provider_auth_records
from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.llm_setup.catalog import SUPPORTED_PROVIDERS
from surfaces.shared.llm_setup.hosted_connection import hosted_model
from surfaces.shared.llm_setup.selection import resolve_provider


@dataclass(frozen=True)
class ModelChoice:
    """A non-secret model choice; duplicate names on different sources stay distinct."""

    provider: str
    model: str
    label: str
    source: str
    active: bool = False
    availability: str = "configured"

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", strip_terminal_controls(self.label))
        object.__setattr__(self, "source", strip_terminal_controls(self.source))

    @property
    def key(self) -> str:
        """Stable exact identity for a terminal picker, including CLI defaults."""
        return f"{self.provider}:{self.model}"


def active_model() -> tuple[str, str]:
    """Describe active routing without resolving stored bearer tokens."""
    hosted = hosted_model()
    if hosted and os.getenv(OPENSRE_LLM_CONNECTION_ENV, "").strip().lower() != "local":
        return HOSTED_MODEL_PROVIDER, hosted
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    configured = os.getenv(LLM_PROVIDER_ENV, "anthropic")
    provider = PROVIDER_BY_VALUE.get(configured)
    if provider is None:
        return configured, ""
    saved = os.getenv(provider.model_env, "").strip() or (
        os.getenv(provider.legacy_model_env, "").strip() if provider.legacy_model_env else ""
    )
    return provider.value, saved or provider.default_model


def provider_choices(
    name: str,
    *,
    record: dict[str, str] | None = None,
    active: tuple[str, str] | None = None,
) -> list[ModelChoice]:
    """Return curated/cached choices and a saved custom model, with no live discovery."""
    provider = resolve_provider(name)
    kinds = {
        "api_key": "API billing",
        "subscription": "Subscription",
        "cli": "CLI-managed",
        "host": "Local",
        "none": "Cloud account",
    }
    source = f"{provider.label} · {kinds[provider.credential_kind]}"
    models = saved_models(name, record=record) or [
        (option.value, option.label) for option in provider.models
    ]
    saved = os.getenv(provider.model_env, "").strip() or (
        os.getenv(provider.legacy_model_env, "").strip() if provider.legacy_model_env else ""
    )
    if saved and saved not in {value for value, _label in models}:
        models = [(saved, saved), *models]
    if not models:
        fallback = (
            "CLI default" if provider.credential_kind == "cli" else "Custom model ID required"
        )
        models = [(provider.default_model, provider.default_model or fallback)]
    active = active_model() if active is None else active
    if active[0] == name and active[1] not in dict(models):
        models = [(active[1], active[1] or "CLI default"), *models]
    return [
        ModelChoice(name, value, label or "CLI default", source, active == (name, value))
        for value, label in dict(models).items()
    ]


def active_choice() -> ModelChoice:
    """Describe the current selection without resolving credentials or live catalogs."""
    provider, model = active_model()
    if provider == HOSTED_MODEL_PROVIDER:
        return ModelChoice(provider, model, model, "OpenSRE hosted · OpenSRE credits", True)
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    if provider in PROVIDER_BY_VALUE:
        return next(row for row in provider_choices(provider) if row.model == model)
    return ModelChoice(provider, model, model, "Unknown provider", True, "unavailable")


def connected_choices(*, provider: str | None = None) -> list[ModelChoice]:
    """List saved connections without launching auth probes or discovering providers."""
    from dataclasses import replace

    rows: list[ModelChoice] = []
    current = active_model()
    records = provider_auth_records()
    hosted = hosted_model()
    if hosted and provider in (None, HOSTED_MODEL_PROVIDER):
        rows.append(
            ModelChoice(
                HOSTED_MODEL_PROVIDER,
                hosted,
                hosted,
                "OpenSRE hosted · OpenSRE credits",
                current == (HOSTED_MODEL_PROVIDER, hosted),
            )
        )
    for option in SUPPORTED_PROVIDERS:
        if provider is not None and option.value != provider:
            continue
        record = records.get(option.value, {})
        auth = connection_status(option.value, record=record)
        if not auth.configured:
            continue
        availability = (
            "Reconnect with /login"
            if auth.stale
            else "configured"
            if auth.verified
            else "Auth checked on use"
        )
        if option.endpoint_env and not os.getenv(option.endpoint_env, "").strip():
            continue
        rows.extend(
            replace(row, availability=availability)
            for row in provider_choices(option.value, record=record, active=current)
        )
    return sorted(rows, key=lambda row: row.provider != current[0])
