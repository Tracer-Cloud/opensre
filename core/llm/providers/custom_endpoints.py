"""Custom OpenAI-/Anthropic-compatible gateway providers.

These providers let OpenSRE point at an arbitrary base URL — a LiteLLM proxy,
vLLM, LocalAI, or an internal model gateway — with the user's own API key and
model name. They exist for self-hosted, proxied, and on-prem deployments where
direct calls to the public OpenAI/Anthropic APIs are not allowed.

``custom-openai`` reuses the OpenAI-compatible client boundary (same path as
openrouter/deepseek). ``custom-anthropic`` uses the Anthropic SDK with a
base-URL override, threaded through both the sync LLM client and the agent
loop. Neither infers behavior from the URL: the OpenAI-vs-Anthropic API surface
is fixed by the provider slug, not by the endpoint.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from config.constants.llm import (
    CUSTOM_ANTHROPIC_BASE_URL_ENV,
    normalize_anthropic_base_url,
)
from core.llm.types import ModelType

CUSTOM_OPENAI_PROVIDER = "custom-openai"
CUSTOM_ANTHROPIC_PROVIDER = "custom-anthropic"

# Settings-attribute prefixes: the hyphenated slugs are not valid Python
# attribute names, so per-tier model lookups use these underscore forms.
CUSTOM_OPENAI_SETTINGS_PREFIX = "custom_openai"
CUSTOM_ANTHROPIC_SETTINGS_PREFIX = "custom_anthropic"


def is_custom_openai_provider(provider: str) -> bool:
    """Return whether *provider* is the custom OpenAI-compatible slug."""
    return provider.strip().lower() == CUSTOM_OPENAI_PROVIDER


def is_custom_anthropic_provider(provider: str) -> bool:
    """Return whether *provider* is the custom Anthropic-compatible slug."""
    return provider.strip().lower() == CUSTOM_ANTHROPIC_PROVIDER


def is_custom_provider(provider: str) -> bool:
    """Return whether *provider* is either custom gateway slug."""
    return is_custom_openai_provider(provider) or is_custom_anthropic_provider(provider)


def custom_settings_prefix(provider: str) -> str:
    """Return the settings-attribute prefix for a custom provider slug."""
    if is_custom_anthropic_provider(provider):
        return CUSTOM_ANTHROPIC_SETTINGS_PREFIX
    return CUSTOM_OPENAI_SETTINGS_PREFIX


def select_custom_model(settings: Any, provider: str, model_type: ModelType) -> str:
    """Return the configured per-tier model for a custom provider."""
    prefix = custom_settings_prefix(provider)
    return str(getattr(settings, f"{prefix}_{model_type}_model"))


def custom_base_url(settings: Any, provider: str) -> str:
    """Return the configured base URL for a custom provider."""
    prefix = custom_settings_prefix(provider)
    return str(getattr(settings, f"{prefix}_base_url"))


def custom_anthropic_probe_base_url() -> str:
    """Normalized ``custom-anthropic`` base URL from the env, for the onboarding probe.

    Owns the "resolve the gateway URL for a live validation" step so the shared
    wizard credential validator stays a thin dispatcher instead of embedding
    provider-specific env reads and normalization.
    """
    return normalize_anthropic_base_url(os.getenv(CUSTOM_ANTHROPIC_BASE_URL_ENV, ""))


def redact_base_url(base_url: str) -> str:
    """Return ``scheme://host[:port]`` only, dropping path, query, and userinfo.

    Custom gateway URLs are user-supplied and can carry a token in the path,
    query, or ``user:pass@`` userinfo; diagnostics log the host only so a debug
    line never leaks a secret.
    """
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if not host:
        return "(unset)"
    if ":" in host:  # bracket an IPv6 literal
        host = f"[{host}]"
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return f"{parts.scheme}://{netloc}"


def log_endpoint_resolution(
    provider: str, base_url: str, model: str, model_type: ModelType
) -> None:
    """Emit a redacted diagnostic so custom-gateway failures are diagnosable.

    Surfaces the resolved provider, redacted base URL (host only — never a token),
    model, and tier for each LLM client build; custom endpoints otherwise fail in
    hard-to-debug ways when only the model name is visible. Routed through the
    project debug channel so ``opensre --debug`` / ``TRACER_VERBOSE=1`` shows it
    (OpenSRE does not configure stdlib ``logging`` levels from the CLI).
    """
    from infrastructure.observability import debug_print

    debug_print(
        f"custom LLM endpoint resolved: provider={provider} "
        f"base_url={redact_base_url(base_url)} model={model} tier={model_type}"
    )
