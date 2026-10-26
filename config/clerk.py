"""Clerk JWT configuration for both development and production environments.

These are public endpoints and issuer URLs, not secrets.
"""

import os

from config.constants.clerk import CLERK_ISSUER_ENV, CLERK_JWKS_URL_ENV
from config.strict_config import StrictConfigModel

__all__ = (
    "CLERK_CONFIG_DEV",
    "CLERK_CONFIG_PROD",
    "ClerkConfig",
    "JWKS_CACHE_TTL_SECONDS",
    "JWT_ALGORITHM",
    "get_clerk_config_override",
)


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

# JWT Configuration
JWT_ALGORITHM = "RS256"
JWKS_CACHE_TTL_SECONDS = 3600


def get_clerk_config_override() -> ClerkConfig | None:
    """Return the Clerk instance configured via CLERK_ISSUER / CLERK_JWKS_URL.

    The org-silo infra injects these per deployment; when ``CLERK_ISSUER`` is
    unset, callers fall back to the hardcoded ``CLERK_CONFIG_DEV`` /
    ``CLERK_CONFIG_PROD`` defaults. ``CLERK_JWKS_URL`` defaults to the
    issuer's standard ``/.well-known/jwks.json`` path when omitted. Read at
    call time (not import time) so env loaded by ``bootstrap_opensre_env``
    and test monkeypatching are honored.
    """
    issuer = os.getenv(CLERK_ISSUER_ENV, "").strip().rstrip("/")
    if not issuer:
        return None
    jwks_url = os.getenv(CLERK_JWKS_URL_ENV, "").strip() or f"{issuer}/.well-known/jwks.json"
    return ClerkConfig(jwks_url=jwks_url, issuer=issuer)
