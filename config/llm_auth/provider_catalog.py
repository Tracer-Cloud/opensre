"""Canonical LLM provider auth and model-selection metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from config.constants.llm import (
    ANTHROPIC_API_KEY_ENV,
    AZURE_OPENAI_API_KEY_ENV,
    AZURE_OPENAI_API_VERSION_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
    CUSTOM_ANTHROPIC_API_KEY_ENV,
    CUSTOM_ANTHROPIC_BASE_URL_ENV,
    CUSTOM_OPENAI_API_KEY_ENV,
    CUSTOM_OPENAI_BASE_URL_ENV,
    DEEPSEEK_API_KEY_ENV,
    GEMINI_API_KEY_ENV,
    GROQ_API_KEY_ENV,
    MINIMAX_API_KEY_ENV,
    NVIDIA_API_KEY_ENV,
    OPENAI_API_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
    TRUSTEDROUTER_API_KEY_ENV,
)


class CredentialKind(StrEnum):
    """How a provider proves its identity to the LLM backend.

    Kept distinct from the wizard's onboarding vocabulary
    (:class:`surfaces.shared.llm_setup.catalog.WizardCredentialKind`): the two share
    ``api_key``/``cli`` but the wizard's ``host``/``none`` map to this enum's
    ``local``/``ambient``. Do not merge them — see the ``WIZARD_TO_CATALOG_KIND``
    translation in ``surfaces/shared/llm_setup/catalog.py``.
    """

    #: A user-supplied API key stored by OpenSRE (credentials file / env).
    API_KEY = "api_key"
    #: A vendor CLI handles its own auth (Codex, Claude Code); no key in .env.
    CLI = "cli"
    #: Credentials the runtime reads from the environment (Bedrock IAM, Vertex ADC).
    AMBIENT = "ambient"
    #: A local host endpoint (Ollama); reachability, not a secret.
    LOCAL = "local"


@dataclass(frozen=True)
class ProviderSpec:
    """Provider facts shared by config, auth, wizard, and runtime checks."""

    value: str
    label: str
    credential_kind: CredentialKind
    api_key_env: str = ""
    model_env: str = ""
    legacy_model_env: str | None = None
    toolcall_model_env: str | None = None
    classification_model_env: str | None = None
    cli_model_env: str | None = None
    endpoint_env: str = ""
    api_version_env: str = ""
    project_env: str = ""
    location_env: str = ""
    allow_custom_models: bool = False

    @property
    def uses_open_sre_api_key(self) -> bool:
        return self.credential_kind == CredentialKind.API_KEY and bool(self.api_key_env)


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        value="anthropic",
        label="Anthropic API key",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=ANTHROPIC_API_KEY_ENV,
        model_env="ANTHROPIC_REASONING_MODEL",
        legacy_model_env="ANTHROPIC_MODEL",
        toolcall_model_env="ANTHROPIC_TOOLCALL_MODEL",
        classification_model_env="ANTHROPIC_CLASSIFICATION_MODEL",
    ),
    ProviderSpec(
        value="openai",
        label="OpenAI API key",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=OPENAI_API_KEY_ENV,
        model_env="OPENAI_REASONING_MODEL",
        legacy_model_env="OPENAI_MODEL",
        toolcall_model_env="OPENAI_TOOLCALL_MODEL",
        classification_model_env="OPENAI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="openrouter",
        label="OpenRouter",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=OPENROUTER_API_KEY_ENV,
        model_env="OPENROUTER_REASONING_MODEL",
        legacy_model_env="OPENROUTER_MODEL",
        toolcall_model_env="OPENROUTER_TOOLCALL_MODEL",
        classification_model_env="OPENROUTER_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="trustedrouter",
        label="TrustedRouter",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=TRUSTEDROUTER_API_KEY_ENV,
        model_env="TRUSTEDROUTER_REASONING_MODEL",
        legacy_model_env="TRUSTEDROUTER_MODEL",
        toolcall_model_env="TRUSTEDROUTER_TOOLCALL_MODEL",
        classification_model_env="TRUSTEDROUTER_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="deepseek",
        label="DeepSeek",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=DEEPSEEK_API_KEY_ENV,
        model_env="DEEPSEEK_REASONING_MODEL",
        legacy_model_env="DEEPSEEK_MODEL",
        toolcall_model_env="DEEPSEEK_TOOLCALL_MODEL",
        classification_model_env="DEEPSEEK_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="gemini",
        label="Google Gemini API key",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=GEMINI_API_KEY_ENV,
        model_env="GEMINI_REASONING_MODEL",
        legacy_model_env="GEMINI_MODEL",
        toolcall_model_env="GEMINI_TOOLCALL_MODEL",
        classification_model_env="GEMINI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="nvidia",
        label="NVIDIA NIM",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=NVIDIA_API_KEY_ENV,
        model_env="NVIDIA_REASONING_MODEL",
        legacy_model_env="NVIDIA_MODEL",
        toolcall_model_env="NVIDIA_TOOLCALL_MODEL",
        classification_model_env="NVIDIA_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="minimax",
        label="MiniMax",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=MINIMAX_API_KEY_ENV,
        model_env="MINIMAX_REASONING_MODEL",
        legacy_model_env="MINIMAX_MODEL",
        toolcall_model_env="MINIMAX_TOOLCALL_MODEL",
        classification_model_env="MINIMAX_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="groq",
        label="Groq API key",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=GROQ_API_KEY_ENV,
        model_env="GROQ_REASONING_MODEL",
        legacy_model_env="GROQ_MODEL",
        toolcall_model_env="GROQ_TOOLCALL_MODEL",
        classification_model_env="GROQ_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="azure-openai",
        label="Azure OpenAI",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=AZURE_OPENAI_API_KEY_ENV,
        model_env="AZURE_OPENAI_REASONING_MODEL",
        legacy_model_env="AZURE_OPENAI_MODEL",
        toolcall_model_env="AZURE_OPENAI_TOOLCALL_MODEL",
        classification_model_env="AZURE_OPENAI_CLASSIFICATION_MODEL",
        endpoint_env=AZURE_OPENAI_BASE_URL_ENV,
        api_version_env=AZURE_OPENAI_API_VERSION_ENV,
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="custom-openai",
        label="Custom OpenAI-compatible endpoint",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=CUSTOM_OPENAI_API_KEY_ENV,
        model_env="CUSTOM_OPENAI_REASONING_MODEL",
        legacy_model_env="CUSTOM_OPENAI_MODEL",
        toolcall_model_env="CUSTOM_OPENAI_TOOLCALL_MODEL",
        classification_model_env="CUSTOM_OPENAI_CLASSIFICATION_MODEL",
        endpoint_env=CUSTOM_OPENAI_BASE_URL_ENV,
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="custom-anthropic",
        label="Custom Anthropic-compatible endpoint",
        credential_kind=CredentialKind.API_KEY,
        api_key_env=CUSTOM_ANTHROPIC_API_KEY_ENV,
        model_env="CUSTOM_ANTHROPIC_REASONING_MODEL",
        legacy_model_env="CUSTOM_ANTHROPIC_MODEL",
        toolcall_model_env="CUSTOM_ANTHROPIC_TOOLCALL_MODEL",
        classification_model_env="CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL",
        endpoint_env=CUSTOM_ANTHROPIC_BASE_URL_ENV,
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="bedrock",
        label="Amazon Bedrock (IAM auth)",
        credential_kind=CredentialKind.AMBIENT,
        model_env="BEDROCK_REASONING_MODEL",
        toolcall_model_env="BEDROCK_TOOLCALL_MODEL",
        classification_model_env="BEDROCK_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="vertex-ai",
        label="Google Vertex AI (ADC auth)",
        credential_kind=CredentialKind.AMBIENT,
        model_env="VERTEX_AI_REASONING_MODEL",
        toolcall_model_env="VERTEX_AI_TOOLCALL_MODEL",
        classification_model_env="VERTEX_AI_CLASSIFICATION_MODEL",
        project_env="VERTEX_AI_PROJECT",
        location_env="VERTEX_AI_LOCATION",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="ollama",
        label="Ollama (local)",
        credential_kind=CredentialKind.LOCAL,
        api_key_env="OLLAMA_HOST",
        model_env="OLLAMA_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="codex",
        label="OpenAI Codex CLI",
        credential_kind=CredentialKind.CLI,
        model_env="CODEX_MODEL",
        cli_model_env="CODEX_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="cursor",
        label="Cursor Agent CLI",
        credential_kind=CredentialKind.CLI,
        model_env="CURSOR_MODEL",
        cli_model_env="CURSOR_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="claude-code",
        label="Anthropic Claude Code CLI",
        credential_kind=CredentialKind.CLI,
        model_env="CLAUDE_CODE_MODEL",
        cli_model_env="CLAUDE_CODE_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="gemini-cli",
        label="Google Gemini CLI",
        credential_kind=CredentialKind.CLI,
        model_env="GEMINI_CLI_MODEL",
        cli_model_env="GEMINI_CLI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="antigravity-cli",
        label="Google Antigravity CLI",
        credential_kind=CredentialKind.CLI,
        model_env="ANTIGRAVITY_CLI_MODEL",
        cli_model_env="ANTIGRAVITY_CLI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="opencode",
        label="OpenCode CLI",
        credential_kind=CredentialKind.CLI,
        model_env="OPENCODE_MODEL",
        cli_model_env="OPENCODE_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="kimi",
        label="Kimi Code CLI",
        credential_kind=CredentialKind.CLI,
        model_env="KIMI_MODEL",
        cli_model_env="KIMI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="copilot",
        label="GitHub Copilot CLI",
        credential_kind=CredentialKind.CLI,
        model_env="COPILOT_MODEL",
        cli_model_env="COPILOT_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="grok-cli",
        label="xAI Grok Build CLI",
        credential_kind=CredentialKind.CLI,
        model_env="GROK_CLI_MODEL",
        cli_model_env="GROK_CLI_MODEL",
        allow_custom_models=True,
    ),
    ProviderSpec(
        value="pi",
        label="Pi CLI (pi.dev, BYOK multi-provider)",
        credential_kind=CredentialKind.CLI,
        model_env="PI_MODEL",
        cli_model_env="PI_MODEL",
        allow_custom_models=True,
    ),
)

PROVIDER_BY_VALUE: dict[str, ProviderSpec] = {spec.value: spec for spec in PROVIDER_SPECS}
SUPPORTED_PROVIDER_VALUES: tuple[str, ...] = tuple(spec.value for spec in PROVIDER_SPECS)
API_KEY_PROVIDER_ENVS: dict[str, str] = {
    spec.value: spec.api_key_env for spec in PROVIDER_SPECS if spec.uses_open_sre_api_key
}
KEYLESS_PROVIDER_VALUES: frozenset[str] = frozenset(
    spec.value for spec in PROVIDER_SPECS if not spec.uses_open_sre_api_key
)
_PROVIDER_BY_API_KEY_ENV: dict[str, str] = {
    api_key_env: provider for provider, api_key_env in API_KEY_PROVIDER_ENVS.items()
}


def provider_spec(provider: str) -> ProviderSpec | None:
    """Return the provider spec for *provider*, if supported."""
    return PROVIDER_BY_VALUE.get(provider.strip().lower())


def provider_for_api_key_env(api_key_env: str) -> str | None:
    """Return the provider id whose OpenSRE-managed API key lives in *api_key_env*."""
    return _PROVIDER_BY_API_KEY_ENV.get(api_key_env.strip().upper())


def require_provider_spec(provider: str) -> ProviderSpec:
    """Return the provider spec or raise ``KeyError`` for unsupported providers."""
    spec = provider_spec(provider)
    if spec is None:
        raise KeyError(provider)
    return spec


__all__ = [
    "API_KEY_PROVIDER_ENVS",
    "CredentialKind",
    "KEYLESS_PROVIDER_VALUES",
    "PROVIDER_BY_VALUE",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "SUPPORTED_PROVIDER_VALUES",
    "provider_for_api_key_env",
    "provider_spec",
    "require_provider_spec",
]
