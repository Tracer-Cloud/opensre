from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest

from app.auth.jwt_auth import AsyncJWKSCache, JWTVerificationError, get_signing_key_from_jwks


@pytest.mark.asyncio
async def test_get_jwks_fetches_once_and_uses_cache_within_ttl() -> None:
    """Lock in JWKS cache behavior so refactors do not refetch per request."""
    cache = AsyncJWKSCache(_cache_ttl=3600)
    jwks_url = "https://example.com/.well-known/jwks.json"
    jwks_payload = {
        "keys": [
            {
                "kid": "kid-1",
                "kty": "RSA",
            }
        ]
    }

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = jwks_payload

    with (
        patch("app.auth.jwt_auth.time.time", side_effect=[1000.0, 1001.0]),
        patch("app.auth.jwt_auth.httpx.AsyncClient") as mock_async_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)
        mock_async_client_cls.return_value.__aenter__.return_value = mock_client

        first = await cache.get_jwks(jwks_url)
        second = await cache.get_jwks(jwks_url)

    assert first == jwks_payload
    assert second == jwks_payload
    assert mock_client.get.await_count == 1
    response.raise_for_status.assert_called_once()


def test_get_signing_key_from_jwks_raises_on_invalid_jwk() -> None:
    """Bad JWK data should raise JWTVerificationError, not a bare Exception."""
    token = pyjwt.encode(
        {"sub": "1"},
        "secret",
        algorithm="HS256",
        headers={"kid": "bad-kid"},
    )
    jwks = {"keys": [{"kid": "bad-kid", "kty": "UNSUPPORTED"}]}

    with pytest.raises(JWTVerificationError, match="Failed to parse JWK"):
        get_signing_key_from_jwks(jwks, token)
