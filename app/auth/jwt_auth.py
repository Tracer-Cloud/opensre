"""JWT authentication with proper async signature verification.

This module handles JWT verification using Clerk's JWKS (JSON Web Key Set).
It verifies both the signature and the issuer to ensure tokens are valid.
Uses async httpx for non-blocking JWKS fetching.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from app.config import (
    CLERK_CONFIG_DEV,
    CLERK_CONFIG_PROD,
    JWKS_CACHE_TTL_SECONDS,
    JWT_ALGORITHM,
    Environment,
    get_environment,
)


@dataclass
class JWTClaims:
    """Validated JWT claims."""

    sub: str  # User ID
    organization: str  # Organization ID
    organization_slug: str
    email: str
    full_name: str
    issuer: str
    exp: int
    iat: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> JWTClaims:
        """Create JWTClaims from decoded payload."""
        return cls(
            sub=payload.get("sub", ""),
            organization=payload.get("organization", ""),
            organization_slug=payload.get("organization_slug", ""),
            email=payload.get("email", ""),
            full_name=payload.get("full_name", ""),
            issuer=payload.get("iss", ""),
            exp=payload.get("exp", 0),
            iat=payload.get("iat", 0),
        )


class JWTVerificationError(Exception):
    """Raised when JWT verification fails."""

    pass


class JWTExpiredError(JWTVerificationError):
    """Raised when JWT has expired."""

    pass


class JWTInvalidIssuerError(JWTVerificationError):
    """Raised when JWT issuer is invalid."""

    pass


class JWTMissingClaimError(JWTVerificationError):
    """Raised when a required claim is missing."""

    pass


@dataclass
class CachedJWKS:
    """Cached JWKS data with TTL."""

    keys: dict[str, Any]
    fetched_at: float


@dataclass
class AsyncJWKSCache:
    """Async JWKS cache with httpx for non-blocking fetches."""

    _cache: dict[str, CachedJWKS] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _cache_ttl: int = JWKS_CACHE_TTL_SECONDS

    def _get_lock(self, jwks_url: str) -> asyncio.Lock:
        """Get or create a lock for the given URL."""
        if jwks_url not in self._locks:
            self._locks[jwks_url] = asyncio.Lock()
        return self._locks[jwks_url]

    async def get_jwks(self, jwks_url: str) -> dict[str, Any]:
        """Fetch JWKS from URL with caching.

        Uses async httpx to avoid blocking the event loop.
        """
        now = time.time()

        # Check cache first (without lock for read)
        if jwks_url in self._cache:
            cached = self._cache[jwks_url]
            if now - cached.fetched_at < self._cache_ttl:
                return cached.keys

        # Need to fetch - use lock to prevent thundering herd
        lock = self._get_lock(jwks_url)
        async with lock:
            # Double-check cache after acquiring lock
            if jwks_url in self._cache:
                cached = self._cache[jwks_url]
                if now - cached.fetched_at < self._cache_ttl:
                    return cached.keys

            # Fetch JWKS asynchronously
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(jwks_url)
                response.raise_for_status()
                jwks_data = response.json()

            # Cache the result
            self._cache[jwks_url] = CachedJWKS(keys=jwks_data, fetched_at=now)
            from typing import cast

            return cast(dict[str, Any], jwks_data)

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()


# Global async JWKS cache
_async_jwks_cache = AsyncJWKSCache()


def get_valid_issuers() -> list[str]:
    """Get list of valid JWT issuers.

    In production, only accept production issuer.
    In development, accept both dev and prod issuers for flexibility.
    """
    env = get_environment()
    if env == Environment.PRODUCTION:
        return [CLERK_CONFIG_PROD.issuer]
    return [CLERK_CONFIG_DEV.issuer, CLERK_CONFIG_PROD.issuer]


def get_jwks_url_for_issuer(issuer: str) -> str | None:
    """Get the JWKS URL for a given issuer."""
    if issuer == CLERK_CONFIG_DEV.issuer:
        return CLERK_CONFIG_DEV.jwks_url
    if issuer == CLERK_CONFIG_PROD.issuer:
        return CLERK_CONFIG_PROD.jwks_url
    return None


def decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification.

    Used to extract the issuer before we know which JWKS to use.
    """
    try:
        from typing import cast

        result = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_aud": False,
            },
        )
        return cast(dict[str, Any], result)
    except jwt.exceptions.DecodeError as e:
        raise JWTVerificationError(f"Invalid JWT format: {e}") from e


def get_signing_key_from_jwks(jwks_data: dict[str, Any], token: str) -> Any:
    """Extract the signing key from JWKS that matches the token's kid."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as e:
        raise JWTVerificationError(f"Invalid JWT header: {e}") from e

    kid = unverified_header.get("kid")
    if not kid:
        raise JWTVerificationError("JWT header missing 'kid'")

    keys = jwks_data.get("keys", [])
    for key_data in keys:
        if key_data.get("kid") == kid:
            try:
                jwk = jwt.PyJWK.from_dict(key_data)
                return jwk.key
            except Exception as e:
                raise JWTVerificationError(f"Failed to parse JWK: {e}") from e

    raise JWTVerificationError(f"No matching key found for kid: {kid}")


async def verify_jwt_async(token: str) -> JWTClaims:
    """Verify JWT signature and claims asynchronously.

    This is the primary verification function - fully async and non-blocking.

    Args:
        token: The JWT token string (without "Bearer " prefix).

    Returns:
        JWTClaims object with validated claims.

    Raises:
        JWTVerificationError: If verification fails.
        JWTExpiredError: If the token has expired.
        JWTInvalidIssuerError: If the issuer is not valid.
        JWTMissingClaimError: If required claims are missing.
    """
    # Decode without verification to get the issuer
    unverified_payload = decode_jwt_payload_unverified(token)
    issuer = unverified_payload.get("iss")

    if not issuer:
        raise JWTMissingClaimError("JWT missing required 'iss' claim")

    # Validate issuer
    valid_issuers = get_valid_issuers()
    if issuer not in valid_issuers:
        raise JWTInvalidIssuerError(f"Invalid issuer '{issuer}'. Expected one of: {valid_issuers}")

    # Get JWKS URL for this issuer
    jwks_url = get_jwks_url_for_issuer(issuer)
    if not jwks_url:
        raise JWTInvalidIssuerError(f"No JWKS URL configured for issuer: {issuer}")

    # Fetch JWKS asynchronously
    try:
        jwks_data = await _async_jwks_cache.get_jwks(jwks_url)
    except httpx.HTTPError as e:
        raise JWTVerificationError(f"Failed to fetch JWKS: {e}") from e

    # Get signing key
    signing_key = get_signing_key_from_jwks(jwks_data, token)

    # Verify the token
    try:
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=[JWT_ALGORITHM],
            issuer=issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "require": ["sub", "exp", "iss"],
            },
        )
    except jwt.ExpiredSignatureError as e:
        raise JWTExpiredError("JWT has expired") from e
    except jwt.InvalidIssuerError as e:
        raise JWTInvalidIssuerError(f"Invalid issuer: {e}") from e
    except jwt.InvalidTokenError as e:
        raise JWTVerificationError(f"Invalid JWT: {e}") from e

    # Validate required claims
    if not payload.get("sub"):
        raise JWTMissingClaimError("JWT missing required 'sub' claim")

    if not payload.get("organization"):
        raise JWTMissingClaimError("JWT missing required 'organization' claim")

    return JWTClaims.from_payload(payload)


def verify_jwt(token: str) -> JWTClaims:
    """Synchronous wrapper for verify_jwt_async.

    DEPRECATED: Use verify_jwt_async directly in async contexts.
    This exists for backwards compatibility but will block the event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already inside an async context (e.g. LangGraph thread) — run in a new loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, verify_jwt_async(token))
                return future.result()
        return loop.run_until_complete(verify_jwt_async(token))
    except RuntimeError:
        return asyncio.run(verify_jwt_async(token))


def _safe_verify_jwt(jwt_token: str) -> JWTClaims | None:
    """Verify JWT and return claims, or None on failure."""
    try:
        return verify_jwt(jwt_token)
    except (JWTVerificationError, JWTExpiredError, JWTInvalidIssuerError, JWTMissingClaimError):
        return None


def extract_org_slug_from_jwt(jwt_token: str) -> str | None:
    """Extract organization slug from JWT token using verified claims."""
    claims = _safe_verify_jwt(jwt_token)
    if not claims:
        return None
    val: Any = claims.organization_slug
    return val if isinstance(val, str) else None


def extract_org_id_from_jwt(jwt_token: str) -> str | None:
    """Extract organization ID from JWT token using verified claims."""
    claims = _safe_verify_jwt(jwt_token)
    if not claims:
        return None
    val: Any = claims.organization
    return val if isinstance(val, str) else None
