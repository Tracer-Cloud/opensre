"""Env-backed LLM provider selection, model tiers, and settings validation.

Resolution is deliberately non-falling-back: the configured provider is the one
that is used, so a missing credential surfaces as a validation error instead of
silently switching backends. Provider *metadata* (auth kind, env var names)
lives in ``config.llm_auth.provider_catalog``; model defaults live in
``config.llm_models``. This module owns only the runtime resolution on top of
them.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from config.constants.llm import (
    AZURE_OPENAI_API_VERSION_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
    CUSTOM_ANTHROPIC_BASE_URL_ENV,
    CUSTOM_OPENAI_BASE_URL_ENV,
    LLM_PROVIDER_ENV,
    normalize_anthropic_base_url,
    normalize_custom_base_url,
)
from config.llm_auth.credentials import status as credential_status
from config.llm_auth.provider_catalog import (
    API_KEY_PROVIDER_ENVS,
    PROVIDER_BY_VALUE,
    SUPPORTED_PROVIDER_VALUES,
)
from config.llm_models import (
    ANTHROPIC_CLASSIFICATION_MODEL,
    ANTHROPIC_REASONING_MODEL,
    ANTHROPIC_TOOLCALL_MODEL,
    AZURE_OPENAI_CLASSIFICATION_MODEL,
    AZURE_OPENAI_REASONING_MODEL,
    AZURE_OPENAI_TOOLCALL_MODEL,
    BEDROCK_CLASSIFICATION_MODEL,
    BEDROCK_REASONING_MODEL,
    BEDROCK_TOOLCALL_MODEL,
    CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL,
    CUSTOM_ANTHROPIC_REASONING_MODEL,
    CUSTOM_ANTHROPIC_TOOLCALL_MODEL,
    CUSTOM_OPENAI_CLASSIFICATION_MODEL,
    CUSTOM_OPENAI_REASONING_MODEL,
    CUSTOM_OPENAI_TOOLCALL_MODEL,
    DEEPSEEK_CLASSIFICATION_MODEL,
    DEEPSEEK_REASONING_MODEL,
    DEEPSEEK_TOOLCALL_MODEL,
    DEFAULT_AZURE_OPENAI_API_VERSION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_VERTEX_AI_LOCATION,
    GEMINI_CLASSIFICATION_MODEL,
    GEMINI_REASONING_MODEL,
    GEMINI_TOOLCALL_MODEL,
    GROQ_CLASSIFICATION_MODEL,
    GROQ_REASONING_MODEL,
    GROQ_TOOLCALL_MODEL,
    MINIMAX_CLASSIFICATION_MODEL,
    MINIMAX_REASONING_MODEL,
    MINIMAX_TOOLCALL_MODEL,
    NVIDIA_CLASSIFICATION_MODEL,
    NVIDIA_REASONING_MODEL,
    NVIDIA_TOOLCALL_MODEL,
    OPENAI_CLASSIFICATION_MODEL,
    OPENAI_REASONING_MODEL,
    OPENAI_TOOLCALL_MODEL,
    OPENROUTER_CLASSIFICATION_MODEL,
    OPENROUTER_REASONING_MODEL,
    OPENROUTER_TOOLCALL_MODEL,
    PROVIDER_MODEL_DEFAULTS,
    TRUSTEDROUTER_CLASSIFICATION_MODEL,
    TRUSTEDROUTER_REASONING_MODEL,
    TRUSTEDROUTER_TOOLCALL_MODEL,
    VERTEX_AI_CLASSIFICATION_MODEL,
    VERTEX_AI_REASONING_MODEL,
    VERTEX_AI_TOOLCALL_MODEL,
)
from config.local_env import bootstrap_opensre_env
from config.strict_config import StrictConfigModel

__all__ = (
    "LLMProvider",
    "LLMResolution",
    "LLMSettings",
    "LLM_PROVIDER_API_KEY_ENVS",
    "PROVIDER_ANTHROPIC",
    "PROVIDER_BEDROCK",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI",
    "PROVIDER_VERTEX_AI",
    "describe_llm_resolution",
    "get_configured_llm_provider",
    "get_llm_provider_api_key_env",
    "has_credentials_for_active_llm_provider",
    "has_user_configured_llm_provider",
    "llm_provider_error_context",
    "resolve_llm_settings",
    "resolve_llm_settings_verbose",
)


LLMProvider = Literal[
    "anthropic",
    "openai",
    "openrouter",
    "trustedrouter",
    "deepseek",
    "gemini",
    "nvidia",
    "ollama",
    "bedrock",
    "minimax",
    "groq",
    "azure-openai",
    "custom-openai",
    "custom-anthropic",
    "vertex-ai",
    "codex",
    "cursor",
    "claude-code",
    "gemini-cli",
    "antigravity-cli",
    "opencode",
    "kimi",
    "copilot",
    "grok-cli",
    "pi",
]

LLM_PROVIDER_API_KEY_ENVS = API_KEY_PROVIDER_ENVS

# Runtime identifiers for ``LLMProvider`` members. Branch on these instead of
# bare string literals when routing on the active provider.
PROVIDER_ANTHROPIC: LLMProvider = "anthropic"
PROVIDER_OPENAI: LLMProvider = "openai"
PROVIDER_BEDROCK: LLMProvider = "bedrock"
PROVIDER_OLLAMA: LLMProvider = "ollama"
PROVIDER_VERTEX_AI: LLMProvider = "vertex-ai"


def get_configured_llm_provider() -> str:
    """Return the active LLM provider from env/project .env."""
    bootstrap_opensre_env(override=False)
    return os.getenv(LLM_PROVIDER_ENV, "anthropic").strip().lower() or "anthropic"


def get_llm_provider_api_key_env(provider: str | None = None) -> str | None:
    """Return the API-key env var required by an LLM provider, if any."""
    provider_name = (provider or get_configured_llm_provider()).strip().lower()
    return LLM_PROVIDER_API_KEY_ENVS.get(provider_name)


def _llm_api_key_payload(provider: str) -> dict[str, str]:
    """Return no secrets; runtime resolves credentials request-time."""
    _ = provider
    return {}


def _resolve_model_env(primary: str, default: str, legacy: str | None = None) -> str:
    """Resolve a model id from primary env, optional legacy env, then *default*.

    An empty primary falls through to ``legacy``. Both ``bootstrap_opensre_env``
    (exports known names as empty strings when unset) and a ``.env`` copied from
    ``.env.example`` (blank tier entries per provider) make the primary name
    present-but-empty, and ``os.getenv(primary, fallback)`` evaluates its
    fallback only when the key is *absent* — leaving anyone who set just
    ``<PROVIDER>_MODEL`` on the built-in default.
    """
    raw = os.getenv(primary, "").strip()
    if not raw and legacy:
        raw = os.getenv(legacy, "").strip()
    return raw or default


def _tiered_model_env_payload() -> dict[str, str]:
    """Build ``{settings_key}_*_model`` keys from the model catalog + ProviderSpec envs."""
    payload: dict[str, str] = {}
    for defaults in PROVIDER_MODEL_DEFAULTS.values():
        if defaults.single_model_settings:
            continue
        spec = PROVIDER_BY_VALUE[defaults.provider]
        key = defaults.settings_key
        reasoning_env = spec.model_env or f"{key.upper()}_REASONING_MODEL"
        payload[f"{key}_reasoning_model"] = _resolve_model_env(
            reasoning_env, defaults.reasoning, spec.legacy_model_env
        )
        classification_env = spec.classification_model_env or f"{key.upper()}_CLASSIFICATION_MODEL"
        payload[f"{key}_classification_model"] = _resolve_model_env(
            classification_env, defaults.classification, spec.legacy_model_env
        )
        toolcall_env = spec.toolcall_model_env or f"{key.upper()}_TOOLCALL_MODEL"
        payload[f"{key}_toolcall_model"] = _resolve_model_env(
            toolcall_env, defaults.toolcall, spec.legacy_model_env
        )
    return payload


def _llm_settings_env_payload(provider: str) -> dict[str, object]:
    """Build the raw env-backed payload used to validate LLM settings."""
    return {
        "provider": provider,
        **_llm_api_key_payload(provider),
        **_tiered_model_env_payload(),
        "azure_openai_base_url": os.getenv(AZURE_OPENAI_BASE_URL_ENV, "").strip(),
        "azure_openai_api_version": os.getenv(
            AZURE_OPENAI_API_VERSION_ENV, DEFAULT_AZURE_OPENAI_API_VERSION
        ).strip()
        or DEFAULT_AZURE_OPENAI_API_VERSION,
        "custom_openai_base_url": os.getenv(CUSTOM_OPENAI_BASE_URL_ENV, "").strip(),
        "custom_anthropic_base_url": os.getenv(CUSTOM_ANTHROPIC_BASE_URL_ENV, "").strip(),
        "vertex_ai_project": os.getenv("VERTEX_AI_PROJECT", "").strip(),
        "vertex_ai_location": os.getenv("VERTEX_AI_LOCATION", DEFAULT_VERTEX_AI_LOCATION).strip()
        or DEFAULT_VERTEX_AI_LOCATION,
        "ollama_model": os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
        or DEFAULT_OLLAMA_MODEL,
        "ollama_host": os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST,
        "max_tokens": os.getenv("LLM_MAX_TOKENS", str(DEFAULT_MAX_TOKENS)),
    }


class LLMSettings(StrictConfigModel):
    """Strict runtime configuration for selecting and authenticating an LLM provider."""

    provider: LLMProvider = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    trustedrouter_api_key: str = ""
    deepseek_api_key: str = ""
    gemini_api_key: str = ""
    nvidia_api_key: str = ""
    minimax_api_key: str = ""
    groq_api_key: str = ""
    azure_openai_api_key: str = ""
    azure_openai_base_url: str = ""
    azure_openai_api_version: str = DEFAULT_AZURE_OPENAI_API_VERSION
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_host: str = DEFAULT_OLLAMA_HOST
    anthropic_reasoning_model: str = ANTHROPIC_REASONING_MODEL
    anthropic_classification_model: str = ANTHROPIC_CLASSIFICATION_MODEL
    anthropic_toolcall_model: str = ANTHROPIC_TOOLCALL_MODEL
    openai_reasoning_model: str = OPENAI_REASONING_MODEL
    openai_classification_model: str = OPENAI_CLASSIFICATION_MODEL
    openai_toolcall_model: str = OPENAI_TOOLCALL_MODEL
    openrouter_reasoning_model: str = OPENROUTER_REASONING_MODEL
    openrouter_classification_model: str = OPENROUTER_CLASSIFICATION_MODEL
    openrouter_toolcall_model: str = OPENROUTER_TOOLCALL_MODEL
    trustedrouter_reasoning_model: str = TRUSTEDROUTER_REASONING_MODEL
    trustedrouter_classification_model: str = TRUSTEDROUTER_CLASSIFICATION_MODEL
    trustedrouter_toolcall_model: str = TRUSTEDROUTER_TOOLCALL_MODEL
    deepseek_reasoning_model: str = DEEPSEEK_REASONING_MODEL
    deepseek_classification_model: str = DEEPSEEK_CLASSIFICATION_MODEL
    deepseek_toolcall_model: str = DEEPSEEK_TOOLCALL_MODEL
    gemini_reasoning_model: str = GEMINI_REASONING_MODEL
    gemini_classification_model: str = GEMINI_CLASSIFICATION_MODEL
    gemini_toolcall_model: str = GEMINI_TOOLCALL_MODEL
    nvidia_reasoning_model: str = NVIDIA_REASONING_MODEL
    nvidia_classification_model: str = NVIDIA_CLASSIFICATION_MODEL
    nvidia_toolcall_model: str = NVIDIA_TOOLCALL_MODEL
    minimax_reasoning_model: str = MINIMAX_REASONING_MODEL
    minimax_classification_model: str = MINIMAX_CLASSIFICATION_MODEL
    minimax_toolcall_model: str = MINIMAX_TOOLCALL_MODEL
    groq_reasoning_model: str = GROQ_REASONING_MODEL
    groq_classification_model: str = GROQ_CLASSIFICATION_MODEL
    groq_toolcall_model: str = GROQ_TOOLCALL_MODEL
    azure_openai_reasoning_model: str = AZURE_OPENAI_REASONING_MODEL
    azure_openai_classification_model: str = AZURE_OPENAI_CLASSIFICATION_MODEL
    azure_openai_toolcall_model: str = AZURE_OPENAI_TOOLCALL_MODEL
    custom_openai_base_url: str = ""
    custom_openai_reasoning_model: str = CUSTOM_OPENAI_REASONING_MODEL
    custom_openai_classification_model: str = CUSTOM_OPENAI_CLASSIFICATION_MODEL
    custom_openai_toolcall_model: str = CUSTOM_OPENAI_TOOLCALL_MODEL
    custom_anthropic_base_url: str = ""
    custom_anthropic_reasoning_model: str = CUSTOM_ANTHROPIC_REASONING_MODEL
    custom_anthropic_classification_model: str = CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL
    custom_anthropic_toolcall_model: str = CUSTOM_ANTHROPIC_TOOLCALL_MODEL
    bedrock_reasoning_model: str = BEDROCK_REASONING_MODEL
    bedrock_classification_model: str = BEDROCK_CLASSIFICATION_MODEL
    bedrock_toolcall_model: str = BEDROCK_TOOLCALL_MODEL
    vertex_ai_project: str = ""
    vertex_ai_location: str = DEFAULT_VERTEX_AI_LOCATION
    vertex_ai_reasoning_model: str = VERTEX_AI_REASONING_MODEL
    vertex_ai_classification_model: str = VERTEX_AI_CLASSIFICATION_MODEL
    vertex_ai_toolcall_model: str = VERTEX_AI_TOOLCALL_MODEL
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, gt=0)

    @field_validator("ollama_host", mode="before")
    @classmethod
    def _normalize_ollama_host(cls, value: object) -> str:
        host = str(value or DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host

    @field_validator("azure_openai_base_url", mode="before")
    @classmethod
    def _normalize_azure_openai_base_url(cls, value: object) -> str:
        from config.llm_auth.azure import normalize_azure_openai_base_url

        return normalize_azure_openai_base_url(str(value or ""))

    @field_validator("custom_openai_base_url", mode="before")
    @classmethod
    def _normalize_custom_openai_base_url(cls, value: object) -> str:
        return normalize_custom_base_url(str(value or ""))

    @field_validator("custom_anthropic_base_url", mode="before")
    @classmethod
    def _normalize_custom_anthropic_base_url(cls, value: object) -> str:
        return normalize_anthropic_base_url(str(value or ""))

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        provider = str(value or "anthropic").strip().lower() or "anthropic"
        valid_providers = SUPPORTED_PROVIDER_VALUES
        if provider in valid_providers:
            return provider
        suggestion = get_close_matches(provider, valid_providers, n=1)
        if suggestion:
            raise ValueError(
                f"Unsupported LLM provider '{provider}'. Did you mean '{suggestion[0]}'?"
            )
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. Expected one of: {', '.join(valid_providers)}."
        )

    @model_validator(mode="after")
    def _require_api_key_for_selected_provider(self) -> "LLMSettings":
        if self.provider == "azure-openai" and not self.azure_openai_base_url:
            raise ValueError(
                "LLM provider 'azure-openai' requires AZURE_OPENAI_BASE_URL to be set."
            )
        if self.provider == "custom-openai":
            if not self.custom_openai_base_url:
                raise ValueError(
                    "LLM provider 'custom-openai' requires CUSTOM_OPENAI_BASE_URL to be set."
                )
            if not all(
                (
                    self.custom_openai_reasoning_model,
                    self.custom_openai_classification_model,
                    self.custom_openai_toolcall_model,
                )
            ):
                raise ValueError(
                    "LLM provider 'custom-openai' requires a model — set CUSTOM_OPENAI_MODEL "
                    "(applied to every tier) or all three CUSTOM_OPENAI_"
                    "{REASONING,CLASSIFICATION,TOOLCALL}_MODEL."
                )
        if self.provider == "custom-anthropic":
            if not self.custom_anthropic_base_url:
                raise ValueError(
                    "LLM provider 'custom-anthropic' requires CUSTOM_ANTHROPIC_BASE_URL to be set."
                )
            if not all(
                (
                    self.custom_anthropic_reasoning_model,
                    self.custom_anthropic_classification_model,
                    self.custom_anthropic_toolcall_model,
                )
            ):
                raise ValueError(
                    "LLM provider 'custom-anthropic' requires a model — set CUSTOM_ANTHROPIC_MODEL "
                    "(applied to every tier) or all three CUSTOM_ANTHROPIC_"
                    "{REASONING,CLASSIFICATION,TOOLCALL}_MODEL."
                )
        return self

    @classmethod
    def from_env(cls) -> "LLMSettings":
        """Build validated LLM settings from environment variables."""
        bootstrap_opensre_env(override=False)
        return cls.model_validate(_llm_settings_env_payload(get_configured_llm_provider()))


@dataclass(frozen=True)
class LLMResolution:
    """Outcome of resolving LLM settings for the configured provider."""

    settings: LLMSettings
    configured_provider: str
    resolved_provider: str
    attempted_providers: tuple[str, ...]
    missing_key_env: str | None

    @property
    def fell_back(self) -> bool:
        """True when the active provider differs from the configured one."""
        return self.resolved_provider != self.configured_provider

    def summary(self) -> str:
        """One-line, user-facing description of the active provider decision."""
        return f"Using configured LLM provider '{self.resolved_provider}'."


def resolve_llm_settings_verbose(
    fallback_providers: Sequence[str] = (),
    *,
    provider_override: str | None = None,
) -> LLMResolution:
    """Resolve LLM settings without implicit provider fallback."""
    bootstrap_opensre_env(override=False)
    _ = fallback_providers
    configured_provider = provider_override or get_configured_llm_provider()
    settings = LLMSettings.model_validate(_llm_settings_env_payload(configured_provider))
    return LLMResolution(
        settings=settings,
        configured_provider=configured_provider,
        resolved_provider=settings.provider,
        attempted_providers=(configured_provider,),
        missing_key_env=None,
    )


def resolve_llm_settings(
    fallback_providers: Sequence[str] = (),
    *,
    provider_override: str | None = None,
) -> LLMSettings:
    """Resolve LLM settings for the configured provider only."""
    return resolve_llm_settings_verbose(
        fallback_providers,
        provider_override=provider_override,
    ).settings


def describe_llm_resolution(
    fallback_providers: Sequence[str] = (),
) -> str:
    """Return a human-readable LLM provider resolution report for diagnostics.

    Safe to call even when no provider has usable credentials: instead of
    raising it reports the missing-credentials condition. Intended for
    ``/status``, doctor commands, and CI diagnostics so operators no longer need
    ad-hoc inline probes to see which provider is actually in use.
    """
    try:
        resolution = resolve_llm_settings_verbose(fallback_providers)
    except ValidationError as exc:
        configured = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(configured)
        detail = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        lines = [
            f"configured provider : {configured}",
            "resolved provider   : <none — no usable provider credentials>",
        ]
        if env_var:
            lines.append(f"required key        : {env_var}")
        lines.append(f"detail              : {detail}")
        return "\n".join(lines)

    lines = [
        f"configured provider : {resolution.configured_provider}",
        f"resolved provider   : {resolution.resolved_provider}",
        "fell back           : no",
        f"providers attempted : {', '.join(resolution.attempted_providers)}",
    ]
    auth_status = credential_status(resolution.resolved_provider)
    lines.append(f"credential status   : {auth_status.source} ({auth_status.detail})")
    return "\n".join(lines)


def llm_provider_error_context(
    fallback_providers: Sequence[str] = (),
) -> str:
    """Return a short bracketed provider context for prefixing error messages.

    Never raises — diagnostics must not mask the original error. Returns an
    empty string when resolution itself fails so callers can fall back to the
    raw provider error untouched.
    """
    try:
        resolution = resolve_llm_settings_verbose(fallback_providers)
    except Exception:
        return ""
    return f"[LLM provider: {resolution.resolved_provider}]"


def has_credentials_for_active_llm_provider() -> bool:
    """Return prompt-safe auth availability for the configured LLM provider."""
    settings = resolve_llm_settings()
    auth_status = credential_status(settings.provider)
    return auth_status.configured and not auth_status.stale


def has_user_configured_llm_provider() -> bool:
    """Return whether the user picked their own LLM provider and it has credentials.

    "Own" means ``LLM_PROVIDER`` is set explicitly, in the environment or the
    persisted ``.env`` the wizard writes. The built-in default provider does not
    count, so a fresh install reports False and callers cannot mistake it for a
    deliberate choice.
    """
    bootstrap_opensre_env(override=False)
    provider = os.getenv(LLM_PROVIDER_ENV, "").strip().lower()
    if not provider:
        return False
    auth_status = credential_status(provider)
    return auth_status.configured and not auth_status.stale
