"""Global application configuration.

Clerk JWT configuration for both development and production environments.
These are public endpoints and issuer URLs, not secrets.
"""

import os
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class LLMModelConfig:
    """Configuration for an LLM provider's model variants."""

    reasoning_model: str
    toolcall_model: str
    max_tokens: int


class Environment(Enum):
    """Application environment."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


@dataclass(frozen=True)
class ClerkConfig:
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

# Gemini model constants
GEMINI_REASONING_MODEL = "gemini-3.1-pro"
GEMINI_TOOLCALL_MODEL = "gemini-3.1-flash"

# NVIDIA NIM model constants
NVIDIA_REASONING_MODEL = "nvidia/nemotron-3-super-120b-a12b"
NVIDIA_TOOLCALL_MODEL = "nvidia/nemotron-3-nano-30b-a3b"

# Base URLs for OpenAI-compatible providers
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
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

# Tracer API Configuration
TRACER_BASE_URL_DEV = "https://staging.tracer.cloud"
TRACER_BASE_URL_PROD = "https://app.tracer.cloud"
SLACK_CHANNEL = "tracer-rca-report-alerts"

def get_tracer_base_url() -> str:
    """Get Tracer base URL for current environment."""
    return TRACER_BASE_URL_PROD if get_environment() == Environment.PRODUCTION else TRACER_BASE_URL_DEV
