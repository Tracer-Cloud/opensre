"""Global application configuration.

Clerk JWT configuration for both development and production environments.
These are public endpoints and issuer URLs, not secrets.
"""

import os
from difflib import get_close_matches
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.llm_credentials import resolve_llm_api_key
from app.strict_config import StrictConfigModel


class LLMModelConfig(StrictConfigModel):
    """Configuration for an LLM provider's model variants."""

    reasoning_model: str
    toolcall_model: str
    max_tokens: int


class Environment(Enum):
    """Application environment."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ClerkConfig(StrictConfigModel):
    """Clerk JWT configuration for a specific environment."""

    jwks_url: str
    issuer: str


CLERK_CONFIG_DEV = ClerkConfig(
    jwks_url="https://superb-jackal-75.clerk.accounts.dev/.well-known/jwks.json",
    issuer="https://superb-jackal-75.clerk.accounts.dev",
)

CLERK_CONFIG_PROD = ClerkConfig(
    jwks_url="https://clerk.tracer.cloud/.well-known/jwks.json",
    issuer="https://clerk.tracer.cloud",
)


def get_environment() -> Environment:
    """Get current environment from ENV variable.

    Returns:
        Environment enum value based on ENV variable.
        Defaults to DEVELOPMENT if not set or unrecognized.
    """
    env_value = os.getenv("ENV", "development").lower()
    if env_value in ("production", "prod"):
        return Environment.PRODUCTION
    return Environment.DEVELOPMENT


def get_clerk_config() -> ClerkConfig:
    """Get Clerk configuration for current environment.

    Returns:
        ClerkConfig for the current environment.
    """
    env = get_environment()
    if env == Environment.PRODUCTION:
        return CLERK_CONFIG_PROD
    return CLERK_CONFIG_DEV


# JWT Configuration
JWT_ALGORITHM = "RS256"
JWKS_CACHE_TTL_SECONDS = 3600

# LLM Model Constants
DEFAULT_MAX_TOKENS = 4096

# Anthropic model constants
ANTHROPIC_REASONING_MODEL = "claude-sonnet-4-6"
ANTHROPIC_TOOLCALL_MODEL = "claude-haiku-4-5-20251001"

# OpenAI model constants
OPENAI_REASONING_MODEL = "gpt-5.4"
OPENAI_TOOLCALL_MODEL = "gpt-5.4-mini"

# OpenRouter model constants
OPENROUTER_REASONING_MODEL = "openrouter/auto"
OPENROUTER_TOOLCALL_MODEL = "openrouter/auto"

# Gemini model constants (Google AI preview IDs; OpenAI-compatible endpoint)
GEMINI_REASONING_MODEL = "gemini-3.1-pro-preview"
GEMINI_TOOLCALL_MODEL = "gemini-3.1-flash-lite-preview"

# NVIDIA NIM model constants
NVIDIA_REASONING_MODEL = "nvidia/nemotron-3-super-120b-a12b"
NVIDIA_TOOLCALL_MODEL = "nvidia/nemotron-3-nano-30b-a3b"

# Base URLs for OpenAI-compatible providers
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Ollama local model constants
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

LLMProvider = Literal["anthropic", "openai", "openrouter", "gemini", "nvidia", "ollama"]


class LLMSettings(StrictConfigModel):
    """Strict runtime configuration for selecting and authenticating an LLM provider."""

    provider: LLMProvider = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    gemini_api_key: str = ""
    nvidia_api_key: str = ""
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_host: str = DEFAULT_OLLAMA_HOST
    anthropic_reasoning_model: str = ANTHROPIC_REASONING_MODEL
    anthropic_toolcall_model: str = ANTHROPIC_TOOLCALL_MODEL
    openai_reasoning_model: str = OPENAI_REASONING_MODEL
    openai_toolcall_model: str = OPENAI_TOOLCALL_MODEL
    openrouter_reasoning_model: str = OPENROUTER_REASONING_MODEL
    openrouter_toolcall_model: str = OPENROUTER_TOOLCALL_MODEL
    gemini_reasoning_model: str = GEMINI_REASONING_MODEL
    gemini_toolcall_model: str = GEMINI_TOOLCALL_MODEL
    nvidia_reasoning_model: str = NVIDIA_REASONING_MODEL
    nvidia_toolcall_model: str = NVIDIA_TOOLCALL_MODEL
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, gt=0)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        provider = str(value or "anthropic").strip().lower() or "anthropic"
        valid_providers = ("anthropic", "openai", "openrouter", "gemini", "nvidia", "ollama")
        if provider in valid_providers:
            return provider
        suggestion = get_close_matches(provider, valid_providers, n=1)
        if suggestion:
            raise ValueError(f"Unsupported LLM provider '{provider}'. Did you mean '{suggestion[0]}'?")
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. Expected one of: {', '.join(valid_providers)}."
        )

    @model_validator(mode="after")
    def _require_api_key_for_selected_provider(self) -> "LLMSettings":
        if self.provider == "ollama":
            return self  # local Ollama server — no API key required
        provider_to_key = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "openrouter": self.openrouter_api_key,
            "gemini": self.gemini_api_key,
            "nvidia": self.nvidia_api_key,
        }
        if provider_to_key[self.provider]:
            return self

        env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "nvidia": "NVIDIA_API_KEY",
        }[self.provider]
        raise ValueError(f"LLM provider '{self.provider}' requires {env_var} to be set.")

    @classmethod
    def from_env(cls) -> "LLMSettings":
        """Build validated LLM settings from environment variables."""
        return cls.model_validate({
            "provider": os.getenv("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic",
            "anthropic_api_key": resolve_llm_api_key("ANTHROPIC_API_KEY"),
            "openai_api_key": resolve_llm_api_key("OPENAI_API_KEY"),
            "openrouter_api_key": resolve_llm_api_key("OPENROUTER_API_KEY"),
            "gemini_api_key": resolve_llm_api_key("GEMINI_API_KEY"),
            "nvidia_api_key": resolve_llm_api_key("NVIDIA_API_KEY"),
            "anthropic_reasoning_model": os.getenv("ANTHROPIC_REASONING_MODEL", ANTHROPIC_REASONING_MODEL).strip()
            or ANTHROPIC_REASONING_MODEL,
            "anthropic_toolcall_model": os.getenv("ANTHROPIC_TOOLCALL_MODEL", ANTHROPIC_TOOLCALL_MODEL).strip()
            or ANTHROPIC_TOOLCALL_MODEL,
            "openai_reasoning_model": os.getenv("OPENAI_REASONING_MODEL", OPENAI_REASONING_MODEL).strip()
            or OPENAI_REASONING_MODEL,
            "openai_toolcall_model": os.getenv("OPENAI_TOOLCALL_MODEL", OPENAI_TOOLCALL_MODEL).strip()
            or OPENAI_TOOLCALL_MODEL,
            "openrouter_reasoning_model": os.getenv(
                "OPENROUTER_REASONING_MODEL",
                os.getenv("OPENROUTER_MODEL", OPENROUTER_REASONING_MODEL),
            ).strip()
            or OPENROUTER_REASONING_MODEL,
            "openrouter_toolcall_model": os.getenv(
                "OPENROUTER_TOOLCALL_MODEL",
                os.getenv("OPENROUTER_MODEL", OPENROUTER_TOOLCALL_MODEL),
            ).strip()
            or OPENROUTER_TOOLCALL_MODEL,
            "gemini_reasoning_model": os.getenv(
                "GEMINI_REASONING_MODEL",
                os.getenv("GEMINI_MODEL", GEMINI_REASONING_MODEL),
            ).strip()
            or GEMINI_REASONING_MODEL,
            "gemini_toolcall_model": os.getenv(
                "GEMINI_TOOLCALL_MODEL",
                os.getenv("GEMINI_MODEL", GEMINI_TOOLCALL_MODEL),
            ).strip()
            or GEMINI_TOOLCALL_MODEL,
            "nvidia_reasoning_model": os.getenv(
                "NVIDIA_REASONING_MODEL",
                os.getenv("NVIDIA_MODEL", NVIDIA_REASONING_MODEL),
            ).strip()
            or NVIDIA_REASONING_MODEL,
            "nvidia_toolcall_model": os.getenv(
                "NVIDIA_TOOLCALL_MODEL",
                os.getenv("NVIDIA_MODEL", NVIDIA_TOOLCALL_MODEL),
            ).strip()
            or NVIDIA_TOOLCALL_MODEL,
            "ollama_model": os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL,
            "ollama_host": os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST,
            "max_tokens": DEFAULT_MAX_TOKENS,
        })
# LLM Provider Configs
ANTHROPIC_LLM_CONFIG = LLMModelConfig(
    reasoning_model=ANTHROPIC_REASONING_MODEL,
    toolcall_model=ANTHROPIC_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

OPENAI_LLM_CONFIG = LLMModelConfig(
    reasoning_model=OPENAI_REASONING_MODEL,
    toolcall_model=OPENAI_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

OPENROUTER_LLM_CONFIG = LLMModelConfig(
    reasoning_model=OPENROUTER_REASONING_MODEL,
    toolcall_model=OPENROUTER_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

GEMINI_LLM_CONFIG = LLMModelConfig(
    reasoning_model=GEMINI_REASONING_MODEL,
    toolcall_model=GEMINI_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

NVIDIA_LLM_CONFIG = LLMModelConfig(
    reasoning_model=NVIDIA_REASONING_MODEL,
    toolcall_model=NVIDIA_TOOLCALL_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

OLLAMA_LLM_CONFIG = LLMModelConfig(
    reasoning_model=DEFAULT_OLLAMA_MODEL,
    toolcall_model=DEFAULT_OLLAMA_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
)

# Tracer API Configuration
TRACER_BASE_URL_DEV = "https://staging.tracer.cloud"
TRACER_BASE_URL_PROD = "https://app.tracer.cloud"
SLACK_CHANNEL = "tracer-rca-report-alerts"


def get_tracer_base_url() -> str:
    """Get Tracer base URL for current environment."""
    return TRACER_BASE_URL_PROD if get_environment() == Environment.PRODUCTION else TRACER_BASE_URL_DEV
