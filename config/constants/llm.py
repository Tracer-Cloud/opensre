"""LLM provider connection env-var names and base-URL value normalizers.

Kept in this leaf module (not ``config.llm_settings``) because modules that
``config.llm_settings`` imports — e.g. ``config.llm_auth.provider_catalog`` —
also need them, so co-locating them would be a cyclic import.

The base-URL normalizers live here (a Tier-4 leaf that imports only stdlib) so
that ``config`` never has to reach up into ``core`` to normalize a value: both
``config.llm_settings`` validators and the ``surfaces`` wizard import them from
here, and ``core`` may import them downward too.
"""

from __future__ import annotations

from typing import Final

# --- Connection env-var names ------------------------------------------------

LLM_PROVIDER_ENV: Final[str] = "LLM_PROVIDER"

#: Legacy key from the removed OAuth auth method. API keys are the only LLM
#: auth path now; env sync strips this from existing ``.env`` files.
LLM_AUTH_METHOD_ENV: Final[str] = "LLM_AUTH_METHOD"

AZURE_OPENAI_BASE_URL_ENV: Final[str] = "AZURE_OPENAI_BASE_URL"
AZURE_OPENAI_API_VERSION_ENV: Final[str] = "AZURE_OPENAI_API_VERSION"
AZURE_OPENAI_API_KEY_ENV: Final[str] = "AZURE_OPENAI_API_KEY"

#: Opt-in provider-native structured outputs (Anthropic ``output_config`` /
#: OpenAI ``chat.completions.parse``). Default off until a live diagnose turn
#: has verified the request shape — silent fallback would double-invoke.
OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV: Final[str] = "OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT"

# Custom OpenAI-/Anthropic-compatible gateways (LiteLLM proxy, vLLM, LocalAI,
# internal model gateways). The base URL is user-supplied, not hard-coded.
CUSTOM_OPENAI_BASE_URL_ENV: Final[str] = "CUSTOM_OPENAI_BASE_URL"
CUSTOM_OPENAI_API_KEY_ENV: Final[str] = "CUSTOM_OPENAI_API_KEY"
CUSTOM_ANTHROPIC_BASE_URL_ENV: Final[str] = "CUSTOM_ANTHROPIC_BASE_URL"
CUSTOM_ANTHROPIC_API_KEY_ENV: Final[str] = "CUSTOM_ANTHROPIC_API_KEY"

# LLM API key env names OpenSRE may persist (keychain migration + catalog).
# Leaf SoT so ``config.secrets`` never imports ``config.llm_auth`` (cyclic).
ANTHROPIC_API_KEY_ENV: Final[str] = "ANTHROPIC_API_KEY"
OPENAI_API_KEY_ENV: Final[str] = "OPENAI_API_KEY"
OPENROUTER_API_KEY_ENV: Final[str] = "OPENROUTER_API_KEY"
TRUSTEDROUTER_API_KEY_ENV: Final[str] = "TRUSTEDROUTER_API_KEY"
DEEPSEEK_API_KEY_ENV: Final[str] = "DEEPSEEK_API_KEY"
GEMINI_API_KEY_ENV: Final[str] = "GEMINI_API_KEY"
NVIDIA_API_KEY_ENV: Final[str] = "NVIDIA_API_KEY"
MINIMAX_API_KEY_ENV: Final[str] = "MINIMAX_API_KEY"
GROQ_API_KEY_ENV: Final[str] = "GROQ_API_KEY"

OPEN_SRE_API_KEY_ENV_NAMES: Final[tuple[str, ...]] = (
    ANTHROPIC_API_KEY_ENV,
    OPENAI_API_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
    TRUSTEDROUTER_API_KEY_ENV,
    DEEPSEEK_API_KEY_ENV,
    GEMINI_API_KEY_ENV,
    NVIDIA_API_KEY_ENV,
    MINIMAX_API_KEY_ENV,
    GROQ_API_KEY_ENV,
    AZURE_OPENAI_API_KEY_ENV,
    CUSTOM_OPENAI_API_KEY_ENV,
    CUSTOM_ANTHROPIC_API_KEY_ENV,
)

# Custom model env-var names: a single ``*_MODEL`` shortcut that fills every
# tier, plus explicit per-tier overrides. Named here so config, the provider
# catalog, and the wizard never re-spell them as string literals.
CUSTOM_OPENAI_MODEL_ENV: Final[str] = "CUSTOM_OPENAI_MODEL"
CUSTOM_OPENAI_REASONING_MODEL_ENV: Final[str] = "CUSTOM_OPENAI_REASONING_MODEL"
CUSTOM_OPENAI_CLASSIFICATION_MODEL_ENV: Final[str] = "CUSTOM_OPENAI_CLASSIFICATION_MODEL"
CUSTOM_OPENAI_TOOLCALL_MODEL_ENV: Final[str] = "CUSTOM_OPENAI_TOOLCALL_MODEL"
CUSTOM_ANTHROPIC_MODEL_ENV: Final[str] = "CUSTOM_ANTHROPIC_MODEL"
CUSTOM_ANTHROPIC_REASONING_MODEL_ENV: Final[str] = "CUSTOM_ANTHROPIC_REASONING_MODEL"
CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL_ENV: Final[str] = "CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL"
CUSTOM_ANTHROPIC_TOOLCALL_MODEL_ENV: Final[str] = "CUSTOM_ANTHROPIC_TOOLCALL_MODEL"

# --- Base-URL value normalizers ----------------------------------------------

_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


def normalize_custom_base_url(value: str) -> str:
    """Normalize a user-supplied gateway base URL.

    Strips surrounding whitespace and a single trailing slash. A missing scheme
    defaults to ``http://`` for loopback hosts (local LiteLLM/vLLM) and
    ``https://`` otherwise. Any path the user supplies (e.g. ``/v1``) is
    preserved verbatim — the OpenAI-compatible client appends ``/chat/completions``
    to it, so the ``/v1`` the user provides is used exactly once.

    Note: the Anthropic SDK appends ``/v1/messages`` itself, so a base URL for
    ``custom-anthropic`` must NOT keep a trailing ``/v1`` — use
    :func:`normalize_anthropic_base_url` for that route.
    """
    base = (value or "").strip()
    if not base:
        return ""
    if not base.startswith(("http://", "https://")):
        host = base.split("/", 1)[0].split(":", 1)[0].lower()
        scheme = "http" if host in _LOOPBACK_HOSTS else "https"
        base = f"{scheme}://{base}"
    return base.rstrip("/")


def normalize_anthropic_base_url(value: str) -> str:
    """Base URL for the Anthropic SDK, which appends ``/v1/messages`` itself.

    Strips a single trailing ``/v1`` so a base URL that mirrors the OpenAI
    ``/v1`` convention (natural for a shared LiteLLM/vLLM proxy) does not become
    ``/v1/v1/messages`` — a silent 404. A deeper passthrough path (e.g.
    ``.../anthropic``) is preserved so the SDK correctly builds
    ``.../anthropic/v1/messages``.
    """
    base = normalize_custom_base_url(value)
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base
