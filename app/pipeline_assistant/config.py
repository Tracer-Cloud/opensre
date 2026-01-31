"""Configuration for pipeline assistant authentication.

Clerk JWT configuration for both development and production environments.
These are public endpoints and issuer URLs, not secrets.
"""

import os
from dataclasses import dataclass
from enum import Enum


class Environment(Enum):
    """Application environment."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


@dataclass(frozen=True)
class ClerkConfig:
    """Clerk JWT configuration for a specific environment."""

    jwks_url: str
    issuer: str


# Clerk configuration constants (public URLs, not secrets)
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


# LLM Configuration
DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 4096

# JWT Configuration
JWT_ALGORITHM = "RS256"
JWKS_CACHE_TTL_SECONDS = 3600  # Cache JWKS for 1 hour
