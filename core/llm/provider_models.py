"""The active (reasoning, tool-call) model pair for a configured provider."""

from __future__ import annotations

import os

from config.llm_auth.provider_catalog import provider_spec

_CLI_DEFAULT_MODEL = "CLI default"
_UNSET_MODEL = "default"


def resolve_provider_models(settings: object, provider: str) -> tuple[str, str]:
    """Return the active ``(reasoning_model, toolcall_model)`` for ``provider``.

    A CLI-backed provider has one model, read from the env var the provider
    catalog names for it. Others read the provider's ``*_model`` setting, or its
    ``*_reasoning_model`` / ``*_toolcall_model`` pair.
    """
    from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE

    subscription = SUBSCRIPTION_BY_VALUE.get(provider)
    if subscription is not None:
        model = os.getenv(subscription.model_env, "").strip() or subscription.default_model
        return model, model
    spec = provider_spec(provider)
    if spec is not None and spec.cli_model_env:
        cli_model = os.getenv(spec.cli_model_env, "").strip() or _CLI_DEFAULT_MODEL
        return (cli_model, cli_model)

    # Settings attributes use the underscore form; the provider slug can be
    # hyphenated (custom-openai, custom-anthropic, azure-openai, vertex-ai).
    attr_prefix = provider.replace("-", "_")
    single_model = str(getattr(settings, f"{attr_prefix}_model", "")).strip()
    if single_model:
        return (single_model, single_model)

    reasoning_model = str(getattr(settings, f"{attr_prefix}_reasoning_model", "")).strip()
    toolcall_model = str(getattr(settings, f"{attr_prefix}_toolcall_model", "")).strip()
    return (
        reasoning_model or _UNSET_MODEL,
        toolcall_model or reasoning_model or _UNSET_MODEL,
    )


__all__ = ["resolve_provider_models"]
