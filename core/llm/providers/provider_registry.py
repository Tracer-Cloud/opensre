"""First-party LLM provider construction data.

One row per provider Tracer ships native clients for. The tool-calling, reasoning,
and LiteLLM builders read this table instead of repeating a per-provider branch, so
adding a first-party provider is a single row rather than an edit in four dispatchers.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.llm_models import ANTHROPIC_LLM_CONFIG, BEDROCK_LLM_CONFIG, OPENAI_LLM_CONFIG
from config.llm_settings import PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_OPENAI


@dataclass(frozen=True)
class FirstPartyProvider:
    """Where a first-party provider's models, token budget, and LiteLLM prefix live."""

    env_prefix: str  # settings attribute prefix: ``openai`` -> ``openai_reasoning_model``
    max_tokens: int
    litellm_prefix: str  # LiteLLM model namespace: ``openai`` -> ``openai/<model>``
    api_key_env: str | None  # LiteLLM credential env var; None when creds come from elsewhere (AWS)


FIRST_PARTY_PROVIDERS: dict[str, FirstPartyProvider] = {
    PROVIDER_ANTHROPIC: FirstPartyProvider(
        "anthropic", ANTHROPIC_LLM_CONFIG.max_tokens, "anthropic", "ANTHROPIC_API_KEY"
    ),
    PROVIDER_OPENAI: FirstPartyProvider(
        "openai", OPENAI_LLM_CONFIG.max_tokens, "openai", "OPENAI_API_KEY"
    ),
    PROVIDER_BEDROCK: FirstPartyProvider("bedrock", BEDROCK_LLM_CONFIG.max_tokens, "bedrock", None),
}
