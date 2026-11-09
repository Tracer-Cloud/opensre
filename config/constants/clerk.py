"""Clerk environment variable names."""

from __future__ import annotations

# Injected by the org-silo infra (ECS task definition) to point JWT verification
# at the silo's own Clerk instance instead of the built-in defaults.
CLERK_ISSUER_ENV = "CLERK_ISSUER"
CLERK_JWKS_URL_ENV = "CLERK_JWKS_URL"

__all__ = [
    "CLERK_ISSUER_ENV",
    "CLERK_JWKS_URL_ENV",
]
