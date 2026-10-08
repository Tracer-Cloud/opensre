"""The setup-time LLM provider catalog: options, models, credential kinds and env names."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from config.constants.llm import (
    AZURE_OPENAI_API_KEY_ENV,
    AZURE_OPENAI_API_VERSION_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
    CUSTOM_ANTHROPIC_API_KEY_ENV,
    CUSTOM_ANTHROPIC_BASE_URL_ENV,
    CUSTOM_OPENAI_API_KEY_ENV,
    CUSTOM_OPENAI_BASE_URL_ENV,
    TRUSTEDROUTER_API_KEY_ENV,
)
from config.llm_auth.provider_catalog import CredentialKind, require_provider_spec
from config.llm_models import (
    ANTHROPIC_REASONING_MODEL,
    AZURE_OPENAI_REASONING_MODEL,
    BEDROCK_REASONING_MODEL,
    DEEPSEEK_REASONING_MODEL,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    GEMINI_REASONING_MODEL,
    GROQ_REASONING_MODEL,
    MINIMAX_REASONING_MODEL,
    NVIDIA_REASONING_MODEL,
    OPENAI_REASONING_MODEL,
    OPENROUTER_REASONING_MODEL,
    TRUSTEDROUTER_REASONING_MODEL,
    VERTEX_AI_REASONING_MODEL,
)
from config.local_env import PROJECT_ROOT as PROJECT_ROOT
from config.local_env import get_project_env_path
from integrations.llm_cli import LLMCLIAdapter

PROJECT_ENV_PATH = get_project_env_path()


class WizardCredentialKind(StrEnum):
    """How onboarding *prompts* for a provider's credential.

    Deliberately kept separate from
    :class:`config.llm_auth.provider_catalog.CredentialKind`, the runtime's
    vocabulary. The two overlap on ``api_key``/``cli`` but the wizard's ``host``
    and ``none`` translate to the catalog's ``local`` and ``ambient`` — see
    ``WIZARD_TO_CATALOG_KIND`` below. Do NOT merge these enums: collapsing
    ``host``→``local`` or ``none``→``ambient`` would silently mislabel Ollama
    (a local host) as ambient env credentials.
    """

    API_KEY = "api_key"
    #: A locally hosted runtime prompted for a host URL, not a secret (Ollama).
    HOST = "host"
    CLI = "cli"
    #: No onboarding prompt; the runtime reads ambient credentials itself.
    NONE = "none"


#: Translation from the wizard's prompt vocabulary to the runtime catalog kinds.
WIZARD_TO_CATALOG_KIND: dict[WizardCredentialKind, CredentialKind] = {
    WizardCredentialKind.API_KEY: CredentialKind.API_KEY,
    WizardCredentialKind.CLI: CredentialKind.CLI,
    WizardCredentialKind.NONE: CredentialKind.AMBIENT,
    WizardCredentialKind.HOST: CredentialKind.LOCAL,
}


@dataclass(frozen=True)
class ModelOption:
    """A selectable default model."""

    value: str
    label: str


@dataclass(frozen=True)
class ProviderOption:
    """Setup metadata for a supported LLM provider."""

    value: str
    label: str
    group: str
    api_key_env: str
    model_env: str
    default_model: str
    models: tuple[ModelOption, ...]
    #: If set, ``sync_provider_env`` also writes this key (same value) for legacy .env files.
    legacy_model_env: str | None = None
    #: Env var that holds the *toolcall* model for this provider. ``None`` for
    #: providers that don't expose a separate toolcall model (e.g. CLI-backed
    #: providers like ``codex``/``claude-code``, or Ollama).
    toolcall_model_env: str | None = None
    #: Env var that holds the *classification* model for this provider. When
    #: unset, ``sync_provider_env`` falls back to replacing ``_REASONING_MODEL``
    #: with ``_CLASSIFICATION_MODEL`` in ``model_env``.
    classification_model_env: str | None = None
    endpoint_env: str = ""
    api_version_env: str = ""
    #: Human-readable name for the credential requested during onboarding. Most
    #: providers want an API key; Ollama wants a host URL. Used as the wizard
    #: prompt label, e.g. ``{label} {credential_label} ({api_key_env})``.
    credential_label: str = "API key"
    #: Whether the credential should be prompted as a secret (hidden input).
    #: API keys are secrets; a local Ollama host URL is not.
    credential_secret: bool = True
    #: Optional hint shown as the default value in the prompt (e.g. the
    #: default Ollama host URL). Empty string means no default.
    credential_default: str = ""
    #: ``cli`` providers use ``adapter_factory`` and vendor auth (no API key in .env).
    credential_kind: WizardCredentialKind = WizardCredentialKind.API_KEY
    adapter_factory: Callable[[], LLMCLIAdapter] | None = None
    #: Whether the CLI should accept model IDs outside the curated quick-pick list.
    #: Use this for providers whose model catalogs are large, account-gated, or
    #: updated independently of OpenSRE releases.
    allow_custom_models: bool = False

    def __post_init__(self) -> None:
        spec = require_provider_spec(self.value)
        catalog_kind = WIZARD_TO_CATALOG_KIND[self.credential_kind]
        mismatches = {
            "api_key_env": (self.api_key_env, spec.api_key_env),
            "model_env": (self.model_env, spec.model_env),
            "legacy_model_env": (self.legacy_model_env, spec.legacy_model_env),
            "toolcall_model_env": (self.toolcall_model_env, spec.toolcall_model_env),
            "classification_model_env": (
                self.classification_model_env,
                spec.classification_model_env,
            ),
            "endpoint_env": (self.endpoint_env, spec.endpoint_env),
            "api_version_env": (self.api_version_env, spec.api_version_env),
            "credential_kind": (catalog_kind, spec.credential_kind),
            "allow_custom_models": (self.allow_custom_models, spec.allow_custom_models),
        }
        drift = {name: values for name, values in mismatches.items() if values[0] != values[1]}
        if drift:
            details = ", ".join(
                f"{name}: {actual!r} != {expected!r}" for name, (actual, expected) in drift.items()
            )
            raise ValueError(
                f"ProviderOption {self.value!r} drifts from provider catalog: {details}"
            )


# Source: https://docs.anthropic.com/en/docs/about-claude/models/overview
ANTHROPIC_MODELS = (
    ModelOption(value=ANTHROPIC_REASONING_MODEL, label="Claude Opus 4.7"),
    ModelOption(value="claude-sonnet-5-5", label="Claude Sonnet 5.5"),
    ModelOption(value="claude-opus-5-5", label="Claude Opus 5.5"),
    ModelOption(value="claude-fable-5-1", label="Claude Fable 5.1"),
    ModelOption(value="claude-fable-5", label="Claude Fable 5 — most capable"),
    ModelOption(value="claude-sonnet-4-6", label="Claude Sonnet 4.6"),
    ModelOption(value="claude-haiku-4-5", label="Claude Haiku 4.5"),
)

# Source: https://platform.openai.com/docs/models
# Codex model IDs are intentionally omitted here: OpenSRE's direct OpenAI
# provider uses Chat Completions, while Codex models require a different API path.
OPENAI_MODELS = (
    ModelOption(value=OPENAI_REASONING_MODEL, label="GPT-5.4 mini"),
    ModelOption(value="gpt-5.6-sol", label="GPT-5.6 Sol — flagship"),
    ModelOption(value="gpt-5.6-terra", label="GPT-5.6 Terra — balanced"),
    ModelOption(value="gpt-5.6-luna", label="GPT-5.6 Luna — cost-efficient"),
    ModelOption(value="gpt-5.5", label="GPT-5.5"),
    ModelOption(value="gpt-5.4", label="GPT-5.4"),
    ModelOption(value="gpt-5.4-nano", label="GPT-5.4 nano"),
)

# Source: https://openrouter.ai/api/v1/models
OPENROUTER_MODELS = (
    ModelOption(value=OPENROUTER_REASONING_MODEL, label="OpenRouter Auto (smart routing)"),
    ModelOption(value="openai/gpt-5.6-sol", label="GPT-5.6 Sol (via OpenRouter)"),
    ModelOption(value="openai/gpt-5.6-terra", label="GPT-5.6 Terra (via OpenRouter)"),
    ModelOption(value="openai/gpt-5.6-luna", label="GPT-5.6 Luna (via OpenRouter)"),
    ModelOption(value="openai/gpt-5.5", label="GPT-5.5 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-opus-4.7", label="Claude Opus 4.7 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-sonnet-4.6", label="Claude Sonnet 4.6 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-haiku-4.5", label="Claude Haiku 4.5 (via OpenRouter)"),
    ModelOption(
        value="google/gemini-3.1-pro-preview", label="Gemini 3.1 Pro (preview, via OpenRouter)"
    ),
    ModelOption(
        value="google/gemini-3-flash-preview", label="Gemini 3 Flash (preview, via OpenRouter)"
    ),
    ModelOption(
        value="google/gemini-3.1-flash-lite-preview",
        label="Gemini 3.1 Flash-Lite (preview, via OpenRouter)",
    ),
    ModelOption(
        value="google/gemini-3.1-flash-image-preview",
        label="Gemini 3.1 Flash Image (preview, via OpenRouter)",
    ),
    ModelOption(
        value="google/gemini-3-pro-image-preview",
        label="Gemini 3 Pro Image (preview, via OpenRouter)",
    ),
    ModelOption(value="meta-llama/llama-4-maverick", label="Llama 4 Maverick (via OpenRouter)"),
    ModelOption(value="meta-llama/llama-4-scout", label="Llama 4 Scout (via OpenRouter)"),
    ModelOption(value="mistralai/mistral-large-2512", label="Mistral Large 3 (via OpenRouter)"),
    ModelOption(value="x-ai/grok-4", label="Grok 4 (via OpenRouter)"),
    ModelOption(value="x-ai/grok-4-fast", label="Grok 4 Fast (via OpenRouter)"),
    ModelOption(value="moonshotai/kimi-k2.5", label="Kimi K2.5 (via OpenRouter)"),
    ModelOption(value="z-ai/glm-4.7", label="GLM 4.7 (via OpenRouter)"),
    ModelOption(value="minimax/minimax-m2", label="MiniMax M2 (via OpenRouter)"),
    ModelOption(value="deepseek/deepseek-v3.2", label="DeepSeek V3.2 (via OpenRouter)"),
    ModelOption(value="qwen/qwen-3.6-plus-preview", label="Qwen 3.6 Plus (via OpenRouter)"),
)

# Source: https://api.trustedrouter.com/v1/models
# The ``trustedrouter/*`` ids are routing policies, not single models: each picks
# an upstream per request and fails over. Concrete vendor ids pin one model.
TRUSTEDROUTER_MODELS = (
    ModelOption(value=TRUSTEDROUTER_REASONING_MODEL, label="TrustedRouter Auto (smart routing)"),
    ModelOption(value="trustedrouter/fast", label="TrustedRouter Fast (low latency)"),
    ModelOption(value="trustedrouter/cheap", label="TrustedRouter Cheap (lowest cost)"),
    ModelOption(value="trustedrouter/zdr", label="TrustedRouter ZDR (zero-retention routes)"),
    ModelOption(value="trustedrouter/e2e", label="TrustedRouter E2E (confidential compute)"),
    ModelOption(value="trustedrouter/eu", label="TrustedRouter EU (EU-hosted routes)"),
    ModelOption(value="anthropic/claude-opus-4-7", label="Claude Opus 4.7 (via TrustedRouter)"),
    ModelOption(value="anthropic/claude-sonnet-4-6", label="Claude Sonnet 4.6 (via TrustedRouter)"),
    ModelOption(value="openai/gpt-5.4-mini", label="GPT-5.4 mini (via TrustedRouter)"),
    ModelOption(value="google/gemini-3.1-pro", label="Gemini 3.1 Pro (via TrustedRouter)"),
    ModelOption(value="deepseek/deepseek-v4-pro", label="DeepSeek V4 Pro (via TrustedRouter)"),
    ModelOption(value="z-ai/glm-4.7", label="GLM 4.7 (via TrustedRouter)"),
)

DEEPSEEK_MODELS = (
    ModelOption(value=DEEPSEEK_REASONING_MODEL, label="DeepSeek V4 Pro"),
    ModelOption(value="deepseek-v4-flash", label="DeepSeek V4 Flash"),
)

GEMINI_MODELS = (
    ModelOption(value=GEMINI_REASONING_MODEL, label="Gemini 3.1 Pro (preview)"),
    ModelOption(value="gemini-3-flash-preview", label="Gemini 3 Flash (preview)"),
    ModelOption(value="gemini-3.1-flash-lite-preview", label="Gemini 3.1 Flash-Lite (preview)"),
    ModelOption(value="gemini-3.1-flash-image-preview", label="Gemini 3.1 Flash Image (preview)"),
    ModelOption(value="gemini-3-pro-image-preview", label="Gemini 3 Pro Image (preview)"),
)

NVIDIA_MODELS = (
    ModelOption(
        value=NVIDIA_REASONING_MODEL,
        label="Nemotron 3 Super 120B (5x higher throughput for agentic AI)",
    ),
    ModelOption(value="nvidia/nemotron-3-nano-30b-a3b", label="Nemotron 3 Nano 30B"),
)

MINIMAX_MODELS = (
    ModelOption(value=MINIMAX_REASONING_MODEL, label="MiniMax M3"),
    ModelOption(value="MiniMax-M2.7-highspeed", label="MiniMax M2.7 highspeed"),
)

GROQ_MODELS = (
    ModelOption(value=GROQ_REASONING_MODEL, label="Llama 3.3 70B Versatile"),
    ModelOption(value="llama-3.1-8b-instant", label="Llama 3.1 8B Instant"),
    ModelOption(value="openai/gpt-oss-120b", label="GPT-OSS 120B"),
    ModelOption(value="openai/gpt-oss-20b", label="GPT-OSS 20B"),
    ModelOption(value="qwen/qwen3-32b", label="Qwen3 32B"),
    ModelOption(value="meta-llama/llama-4-scout-17b-16e-instruct", label="Llama 4 Scout 17B"),
)

BEDROCK_MODELS = (
    ModelOption(
        value=BEDROCK_REASONING_MODEL,
        label="Claude Sonnet 4.6 (US cross-region) — default",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-7",
        label="Claude Opus 4.7 (US cross-region) — most capable",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-6-v1",
        label="Claude Opus 4.6 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-5-20251101-v1:0",
        label="Claude Opus 4.5 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-opus-4-1-20250805-v1:0",
        label="Claude Opus 4.1 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        label="Claude Sonnet 4.5 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-sonnet-4-20250514-v1:0",
        label="Claude Sonnet 4 (US cross-region)",
    ),
    ModelOption(
        value="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        label="Claude Haiku 4.5 (US cross-region) — fast, cost-efficient",
    ),
    ModelOption(
        value="us.meta.llama4-maverick-17b-instruct-v1:0",
        label="Llama 4 Maverick 17B (US cross-region)",
    ),
    ModelOption(
        value="us.amazon.nova-pro-v1:0",
        label="Amazon Nova Pro (US cross-region)",
    ),
    ModelOption(
        value="mistral.mistral-large-3-675b-instruct",
        label="Mistral Large 3 675B Instruct (on-demand)",
    ),
)

VERTEX_AI_MODELS = (
    ModelOption(value=VERTEX_AI_REASONING_MODEL, label="Gemini 2.5 Pro (Vertex) — default"),
    ModelOption(value="gemini-2.5-flash", label="Gemini 2.5 Flash (Vertex)"),
    ModelOption(value="gemini-2.5-flash-lite", label="Gemini 2.5 Flash-Lite (Vertex)"),
    # Gemini 3.x is Preview-only in Vertex Model Garden as of the July 2026 release
    # notes (docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes) — not
    # curated as the default. Same IDs as the direct Gemini provider's GEMINI_MODELS,
    # since Vertex serves Gemini under the same publisher-model IDs.
    ModelOption(value="gemini-3.1-pro-preview", label="Gemini 3.1 Pro (Vertex, preview)"),
    ModelOption(value="gemini-3-flash-preview", label="Gemini 3 Flash (Vertex, preview)"),
    ModelOption(
        value="gemini-3.1-flash-lite-preview", label="Gemini 3.1 Flash-Lite (Vertex, preview)"
    ),
)

OLLAMA_MODELS = (
    ModelOption(value="llama3.2", label="Llama 3.2 (3B) — recommended"),
    ModelOption(value="llama3.1:8b", label="Llama 3.1 (8B)"),
    ModelOption(value="mistral", label="Mistral 7B"),
    ModelOption(value="qwen2.5:7b", label="Qwen 2.5 (7B)"),
)

# Source: https://platform.claude.com/docs/en/about-claude/models/overview (verified 2026-05-21).
# Empty value means "no --model" so Claude Code uses its configured default.
CLAUDE_CODE_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Claude Code configured model)",
    ),
    ModelOption(value="claude-sonnet-5-5", label="Claude Sonnet 5.5 — balanced"),
    ModelOption(value="claude-opus-5-5", label="Claude Opus 5.5"),
    ModelOption(value="claude-fable-5-1", label="Claude Fable 5.1"),
    ModelOption(value="claude-fable-5", label="Claude Fable 5 — most capable"),
    ModelOption(value="claude-opus-4-7", label="Claude Opus 4.7"),
    ModelOption(value="claude-sonnet-4-6", label="Claude Sonnet 4.6 — balanced"),
    ModelOption(value="claude-haiku-4-5", label="Claude Haiku 4.5 — fast, cost-efficient"),
)

# Source: https://developers.openai.com/codex/cli/features (verified 2026-05-21).
# Empty value means "no -m" so the Codex CLI uses its configured default/current model.
CODEX_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no -m; use Codex configured model)",
    ),
    ModelOption(value="gpt-5.6-sol", label="gpt-5.6-sol — newest frontier coding"),
    ModelOption(value="gpt-5.6-terra", label="gpt-5.6-terra — balanced"),
    ModelOption(value="gpt-5.6-luna", label="gpt-5.6-luna — fast, cost-efficient"),
    ModelOption(value="gpt-5.5", label="gpt-5.5 — frontier coding"),
    ModelOption(value="gpt-5.4", label="gpt-5.4 — fallback default"),
    ModelOption(value="gpt-5.4-mini", label="gpt-5.4-mini — fast, cost-efficient"),
    ModelOption(value="gpt-5.3-codex", label="gpt-5.3-codex — coding-optimized"),
    ModelOption(
        value="gpt-5.3-codex-spark",
        label="gpt-5.3-codex-spark — research preview (ChatGPT Pro)",
    ),
)

# Source: google-gemini/gemini-cli, packages/core/src/config/models.ts (verified 2026-05-21).
# Empty value means "no --model" so Gemini CLI uses its configured/default model.
GEMINI_CLI_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Gemini CLI configured model)",
    ),
    ModelOption(
        value="gemini-3.1-pro-preview",
        label="gemini-3.1-pro-preview — newest frontier (preview)",
    ),
    ModelOption(
        value="gemini-3-flash-preview",
        label="gemini-3-flash-preview — fast (preview)",
    ),
    ModelOption(
        value="gemini-3.1-flash-lite-preview",
        label="gemini-3.1-flash-lite-preview — fastest (preview)",
    ),
    ModelOption(
        value="gemini-2.5-pro",
        label="gemini-2.5-pro — stable, strongest reasoning",
    ),
    ModelOption(
        value="gemini-2.5-flash",
        label="gemini-2.5-flash — stable, balanced",
    ),
    ModelOption(
        value="gemini-2.5-flash-lite",
        label="gemini-2.5-flash-lite — stable, fastest",
    ),
)

# Source: ``agy`` ``/models`` (verified 2026-05-26 against agy 1.0.2).
# Empty value means "no --model" so agy uses its currently configured model.
# Note: agy 1.0.2 does not yet expose ``--model`` in headless ``-p`` mode
# (verified locally), so the adapter ignores any value via ``del model`` in
# ``build()``. Catalog is forward-compat: once Google ships ``--model`` in
# headless, the wizard selection plus ``model_env_key="ANTIGRAVITY_CLI_MODEL"``
# will route through to ``argv`` in a one-line change to ``antigravity_cli.py``.
# Effort variants (Low/Medium/High/Thinking) in ``/models`` use the existing
# ``reasoning_effort`` knob.
ANTIGRAVITY_CLI_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use agy's currently configured model)",
    ),
    ModelOption(value="gemini-3.5-flash", label="gemini-3.5-flash — fast"),
    ModelOption(value="gemini-3.1-pro", label="gemini-3.1-pro — strongest Google reasoning"),
    ModelOption(value="claude-sonnet-4.6", label="claude-sonnet-4.6 — Anthropic balanced"),
    ModelOption(value="claude-opus-4.6", label="claude-opus-4.6 — Anthropic most capable"),
    ModelOption(value="gpt-oss-120b", label="gpt-oss-120b — open-source"),
)

# Source: https://opencode.ai/docs/zen (verified 2026-05-21).
# OpenCode routes models through OpenCode Zen using the ``opencode/`` prefix.
# Curated subset of the full ~40-model catalog; the wizard's custom-ID escape
# hatch covers anything not pre-listed here.
OPENCODE_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no -m; use OpenCode configured model)",
    ),
    ModelOption(value="opencode/gpt-5.5", label="GPT-5.5 (OpenCode Zen) — frontier"),
    ModelOption(value="opencode/gpt-5.4", label="GPT-5.4 (OpenCode Zen)"),
    ModelOption(value="opencode/gpt-5.4-mini", label="GPT-5.4 mini (OpenCode Zen) — fast"),
    ModelOption(
        value="opencode/gpt-5.3-codex",
        label="GPT-5.3 Codex (OpenCode Zen) — coding-optimized",
    ),
    ModelOption(
        value="opencode/gpt-5.3-codex-spark",
        label="GPT-5.3 Codex Spark (OpenCode Zen) — research preview",
    ),
    ModelOption(
        value="opencode/claude-opus-4-7",
        label="Claude Opus 4.7 (OpenCode Zen) — most capable",
    ),
    ModelOption(
        value="opencode/claude-sonnet-4-6",
        label="Claude Sonnet 4.6 (OpenCode Zen) — balanced",
    ),
    ModelOption(
        value="opencode/claude-haiku-4-5",
        label="Claude Haiku 4.5 (OpenCode Zen) — fast",
    ),
    ModelOption(value="opencode/gemini-3.1-pro", label="Gemini 3.1 Pro (OpenCode Zen)"),
    ModelOption(value="opencode/gemini-3-flash", label="Gemini 3 Flash (OpenCode Zen)"),
    ModelOption(value="opencode/kimi-k2.6", label="Kimi K2.6 (OpenCode Zen)"),
    ModelOption(value="opencode/minimax-m2.7", label="MiniMax M2.7 (OpenCode Zen)"),
    ModelOption(value="opencode/qwen3.6-plus", label="Qwen3.6 Plus (OpenCode Zen)"),
    ModelOption(value="opencode/glm-5.1", label="GLM 5.1 (OpenCode Zen)"),
    ModelOption(
        value="opencode/minimax-m2.5-free",
        label="MiniMax M2.5 (OpenCode Zen) — free tier",
    ),
    ModelOption(
        value="opencode/deepseek-v4-flash-free",
        label="DeepSeek V4 Flash (OpenCode Zen) — free tier",
    ),
)


CURSOR_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Cursor configured model)",
    ),
    ModelOption(value="auto", label="auto"),
    ModelOption(value="gpt-5", label="gpt-5"),
    ModelOption(value="sonnet-4", label="sonnet-4"),
    ModelOption(value="sonnet-4-thinking", label="sonnet-4-thinking"),
)


def _codex_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import CodexAdapter

    return CodexAdapter()


def _cursor_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import CursorAdapter

    return CursorAdapter()


def _claude_code_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def _gemini_cli_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import GeminiCLIAdapter

    return GeminiCLIAdapter()


def _antigravity_cli_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import AntigravityCLIAdapter

    return AntigravityCLIAdapter()


def _opencode_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import OpenCodeAdapter

    return OpenCodeAdapter()


def _kimi_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import KimiAdapter

    return KimiAdapter()


def _copilot_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import CopilotAdapter

    return CopilotAdapter()


def _grok_cli_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import GrokCLIAdapter

    return GrokCLIAdapter()


_GROK_CLI_DEFAULT_MODEL_OPTION = ModelOption(
    value="",
    label="CLI default (no -m; use Grok Build configured model)",
)

# Static fallback used when ``grok models`` is unavailable at wizard time.
GROK_CLI_MODELS = (
    _GROK_CLI_DEFAULT_MODEL_OPTION,
    ModelOption(value="grok-build", label="grok-build"),
    ModelOption(value="grok-composer-2.5-fast", label="grok-composer-2.5-fast"),
)


def _pi_adapter_factory() -> LLMCLIAdapter:
    from integrations.llm_cli import PiAdapter

    return PiAdapter()


# Pi is BYOK/multi-provider; models use the ``provider/model`` form. These are a
# convenience shortlist — ``allow_custom_models=True`` lets users type any model
# (and PI_MODEL overrides at runtime). Run ``pi --list-models`` for the full set.
PI_MODELS = (
    ModelOption(value="", label="CLI default (no --model; use Pi configured model)"),
    ModelOption(value="google/gemini-2.5-flash-lite", label="google/gemini-2.5-flash-lite"),
    ModelOption(value="google/gemini-2.5-flash", label="google/gemini-2.5-flash"),
    ModelOption(value="anthropic/claude-haiku-4-5", label="anthropic/claude-haiku-4-5"),
    ModelOption(value="openai/gpt-4o-mini", label="openai/gpt-4o-mini"),
)


KIMI_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no -m; use Kimi configured model)",
    ),
    ModelOption(value="kimi-k2-thinking-turbo", label="kimi-k2-thinking-turbo"),
    ModelOption(value="kimi-k2.5", label="kimi-k2.5"),
    ModelOption(value="kimi-k2.6", label="kimi-k2.6"),
)


# Empty value means "no --model" so Copilot CLI uses its configured default model.
# We do not hardcode model identifiers here: the Copilot CLI's accepted --model
# values are not stable across releases and live behind GitHub-side gating, so
# baking them in risks "model not found" errors after the user has finished the
# wizard. Users override via COPILOT_MODEL when they know what their plan exposes.
COPILOT_MODELS = (
    ModelOption(
        value="",
        label="CLI default (no --model; use Copilot CLI configured model)",
    ),
)


SUPPORTED_PROVIDERS = (
    ProviderOption(
        value="anthropic",
        label="Anthropic API key",
        group="Hosted providers",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_REASONING_MODEL",
        default_model=ANTHROPIC_REASONING_MODEL,
        models=ANTHROPIC_MODELS,
        legacy_model_env="ANTHROPIC_MODEL",
        toolcall_model_env="ANTHROPIC_TOOLCALL_MODEL",
        classification_model_env="ANTHROPIC_CLASSIFICATION_MODEL",
    ),
    ProviderOption(
        value="claude-code",
        label="Anthropic Claude Code CLI",
        group="Hosted providers",
        api_key_env="",
        model_env="CLAUDE_CODE_MODEL",
        default_model="",
        models=CLAUDE_CODE_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_claude_code_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="openai",
        label="OpenAI API key",
        group="Hosted providers",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_REASONING_MODEL",
        default_model=OPENAI_REASONING_MODEL,
        models=OPENAI_MODELS,
        legacy_model_env="OPENAI_MODEL",
        toolcall_model_env="OPENAI_TOOLCALL_MODEL",
        classification_model_env="OPENAI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="codex",
        label="OpenAI Codex CLI",
        group="Hosted providers",
        api_key_env="",
        model_env="CODEX_MODEL",
        default_model="",
        models=CODEX_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_codex_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="openrouter",
        label="OpenRouter",
        group="Hosted providers",
        api_key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_REASONING_MODEL",
        default_model=OPENROUTER_REASONING_MODEL,
        models=OPENROUTER_MODELS,
        legacy_model_env="OPENROUTER_MODEL",
        toolcall_model_env="OPENROUTER_TOOLCALL_MODEL",
        classification_model_env="OPENROUTER_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="trustedrouter",
        label="TrustedRouter",
        group="Hosted providers",
        api_key_env=TRUSTEDROUTER_API_KEY_ENV,
        model_env="TRUSTEDROUTER_REASONING_MODEL",
        default_model=TRUSTEDROUTER_REASONING_MODEL,
        models=TRUSTEDROUTER_MODELS,
        legacy_model_env="TRUSTEDROUTER_MODEL",
        toolcall_model_env="TRUSTEDROUTER_TOOLCALL_MODEL",
        classification_model_env="TRUSTEDROUTER_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="deepseek",
        label="DeepSeek",
        group="Hosted providers",
        api_key_env="DEEPSEEK_API_KEY",
        model_env="DEEPSEEK_REASONING_MODEL",
        default_model=DEEPSEEK_REASONING_MODEL,
        models=DEEPSEEK_MODELS,
        legacy_model_env="DEEPSEEK_MODEL",
        toolcall_model_env="DEEPSEEK_TOOLCALL_MODEL",
        classification_model_env="DEEPSEEK_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="gemini",
        label="Google Gemini API key",
        group="Hosted providers",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_REASONING_MODEL",
        default_model=GEMINI_REASONING_MODEL,
        models=GEMINI_MODELS,
        legacy_model_env="GEMINI_MODEL",
        toolcall_model_env="GEMINI_TOOLCALL_MODEL",
        classification_model_env="GEMINI_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="gemini-cli",
        label="Google Gemini CLI",
        group="Hosted providers",
        api_key_env="",
        model_env="GEMINI_CLI_MODEL",
        default_model="",
        models=GEMINI_CLI_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_gemini_cli_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="antigravity-cli",
        label="Google Antigravity CLI",
        group="Hosted providers",
        api_key_env="",
        model_env="ANTIGRAVITY_CLI_MODEL",
        default_model="",
        models=ANTIGRAVITY_CLI_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_antigravity_cli_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="nvidia",
        label="NVIDIA NIM",
        group="Hosted providers",
        api_key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_REASONING_MODEL",
        default_model=NVIDIA_REASONING_MODEL,
        models=NVIDIA_MODELS,
        legacy_model_env="NVIDIA_MODEL",
        toolcall_model_env="NVIDIA_TOOLCALL_MODEL",
        classification_model_env="NVIDIA_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="minimax",
        label="MiniMax",
        group="Hosted providers",
        api_key_env="MINIMAX_API_KEY",
        model_env="MINIMAX_REASONING_MODEL",
        default_model=MINIMAX_REASONING_MODEL,
        models=MINIMAX_MODELS,
        legacy_model_env="MINIMAX_MODEL",
        toolcall_model_env="MINIMAX_TOOLCALL_MODEL",
        classification_model_env="MINIMAX_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="bedrock",
        label="Amazon Bedrock (IAM auth)",
        group="Hosted providers",
        # Intentionally empty: Bedrock authenticates via the IAM credential
        # chain (env, ~/.aws/credentials, instance profile) — no API key to
        # prompt for.  Empty string is safe: every downstream check uses
        # ``bool(provider.api_key_env)`` or ``.get()`` (never subscript).
        api_key_env="",
        model_env="BEDROCK_REASONING_MODEL",
        default_model=BEDROCK_REASONING_MODEL,
        models=BEDROCK_MODELS,
        toolcall_model_env="BEDROCK_TOOLCALL_MODEL",
        classification_model_env="BEDROCK_CLASSIFICATION_MODEL",
        credential_label="AWS region (uses IAM credentials)",
        credential_secret=False,
        # credential_kind=WizardCredentialKind.NONE causes flow.py to skip the credential prompt
        # entirely.  Region is picked up from AWS_DEFAULT_REGION / ~/.aws/config.
        credential_kind=WizardCredentialKind.NONE,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="vertex-ai",
        label="Google Vertex AI (ADC auth)",
        group="Hosted providers",
        # Intentionally empty: Vertex AI authenticates via Google Application
        # Default Credentials (gcloud ADC, a service-account key, or GCE/GKE
        # metadata) — no API key to prompt for, same as Bedrock's IAM auth.
        api_key_env="",
        model_env="VERTEX_AI_REASONING_MODEL",
        default_model=VERTEX_AI_REASONING_MODEL,
        models=VERTEX_AI_MODELS,
        toolcall_model_env="VERTEX_AI_TOOLCALL_MODEL",
        classification_model_env="VERTEX_AI_CLASSIFICATION_MODEL",
        credential_label="GCP project + region (uses Application Default Credentials)",
        credential_secret=False,
        # credential_kind=WizardCredentialKind.NONE causes flow.py to skip the credential prompt
        # entirely. Project/region are picked up from VERTEX_AI_PROJECT /
        # VERTEX_AI_LOCATION, set outside the wizard (same as Bedrock's region).
        credential_kind=WizardCredentialKind.NONE,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="groq",
        label="Groq API key",
        group="Hosted providers",
        api_key_env="GROQ_API_KEY",
        model_env="GROQ_REASONING_MODEL",
        default_model=GROQ_REASONING_MODEL,
        models=GROQ_MODELS,
        legacy_model_env="GROQ_MODEL",
        toolcall_model_env="GROQ_TOOLCALL_MODEL",
        classification_model_env="GROQ_CLASSIFICATION_MODEL",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="azure-openai",
        label="Azure OpenAI",
        group="Hosted providers",
        api_key_env=AZURE_OPENAI_API_KEY_ENV,
        model_env="AZURE_OPENAI_REASONING_MODEL",
        default_model=AZURE_OPENAI_REASONING_MODEL,
        models=(),
        legacy_model_env="AZURE_OPENAI_MODEL",
        toolcall_model_env="AZURE_OPENAI_TOOLCALL_MODEL",
        classification_model_env="AZURE_OPENAI_CLASSIFICATION_MODEL",
        endpoint_env=AZURE_OPENAI_BASE_URL_ENV,
        api_version_env=AZURE_OPENAI_API_VERSION_ENV,
        credential_default="https://your-resource.openai.azure.com",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="custom-openai",
        label="Custom OpenAI-compatible endpoint",
        group="Hosted providers",
        api_key_env=CUSTOM_OPENAI_API_KEY_ENV,
        model_env="CUSTOM_OPENAI_REASONING_MODEL",
        default_model="",
        models=(),
        legacy_model_env="CUSTOM_OPENAI_MODEL",
        toolcall_model_env="CUSTOM_OPENAI_TOOLCALL_MODEL",
        classification_model_env="CUSTOM_OPENAI_CLASSIFICATION_MODEL",
        endpoint_env=CUSTOM_OPENAI_BASE_URL_ENV,
        credential_default="http://localhost:4000/v1",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="custom-anthropic",
        label="Custom Anthropic-compatible endpoint",
        group="Hosted providers",
        api_key_env=CUSTOM_ANTHROPIC_API_KEY_ENV,
        model_env="CUSTOM_ANTHROPIC_REASONING_MODEL",
        default_model="",
        models=(),
        legacy_model_env="CUSTOM_ANTHROPIC_MODEL",
        toolcall_model_env="CUSTOM_ANTHROPIC_TOOLCALL_MODEL",
        classification_model_env="CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL",
        endpoint_env=CUSTOM_ANTHROPIC_BASE_URL_ENV,
        credential_default="https://proxy.example.com",
        allow_custom_models=True,
    ),
    ProviderOption(
        value="grok-cli",
        label="xAI Grok Build CLI",
        group="Hosted providers",
        api_key_env="",
        model_env="GROK_CLI_MODEL",
        default_model="",
        models=GROK_CLI_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_grok_cli_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="cursor",
        label="Cursor Agent CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="CURSOR_MODEL",
        default_model="auto",
        models=CURSOR_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_cursor_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="opencode",
        label="OpenCode CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="OPENCODE_MODEL",
        default_model="",
        models=OPENCODE_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_opencode_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="kimi",
        label="Kimi Code CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="KIMI_MODEL",
        default_model="",
        models=KIMI_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_kimi_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="copilot",
        label="GitHub Copilot CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="COPILOT_MODEL",
        default_model="",
        models=COPILOT_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_copilot_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="pi",
        label="Pi CLI (pi.dev, BYOK multi-provider)",
        group="Local CLI providers",
        api_key_env="",
        model_env="PI_MODEL",
        default_model="",
        models=PI_MODELS,
        credential_kind=WizardCredentialKind.CLI,
        credential_secret=False,
        adapter_factory=_pi_adapter_factory,
        allow_custom_models=True,
    ),
    ProviderOption(
        value="ollama",
        label="Ollama (local)",
        group="Local providers",
        api_key_env="OLLAMA_HOST",
        model_env="OLLAMA_MODEL",
        default_model=DEFAULT_OLLAMA_MODEL,
        models=OLLAMA_MODELS,
        credential_label="host URL",
        credential_secret=False,
        credential_default=DEFAULT_OLLAMA_HOST,
        credential_kind=WizardCredentialKind.HOST,
        allow_custom_models=True,
    ),
)

PROVIDER_BY_VALUE = {provider.value: provider for provider in SUPPORTED_PROVIDERS}
