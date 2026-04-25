"""Unit tests for JWT authentication."""

import time
import pytest
from unittest.mock import MagicMock, patch
import jwt
import httpx

from app.auth.jwt_auth import (
    JWTClaims,
    AsyncJWKSCache,
    verify_jwt_async,
    JWTVerificationError,
    JWTExpiredError,
    JWTInvalidIssuerError,
)
from app.config import CLERK_CONFIG_DEV, JWT_ALGORITHM


@pytest.fixture
def mock_jwks():
    return {
        "keys": [
            {
                "kid": "test_kid",
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "n": "test_n",
                "e": "AQAB"
            }
        ]
    }


@pytest.mark.asyncio
async def test_jwks_cache_fetch(mock_jwks):
    """Test fetching and caching JWKS."""
    cache = AsyncJWKSCache(_cache_ttl=60)
    
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200, 
            json=lambda: mock_jwks,
            raise_for_status=lambda: None
        )
        
        # First fetch
        keys1 = await cache.get_jwks("https://example.com/jwks")
        assert keys1 == mock_jwks
        assert mock_get.call_count == 1
        
        # Second fetch (should be cached)
        keys2 = await cache.get_jwks("https://example.com/jwks")
        assert keys2 == mock_jwks
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_verify_jwt_async_success(mock_jwks):
    """Test successful JWT verification."""
    # Create a dummy token with the right header
    token = jwt.encode(
        {"iss": CLERK_CONFIG_DEV.issuer, "sub": "user_123", "organization": "org_123", "exp": int(time.time()) + 3600},
        "secret",
        algorithm=JWT_ALGORITHM,
        headers={"kid": "test_kid"}
    )
    
    with patch("app.auth.jwt_auth.decode_jwt_payload_unverified") as mock_decode_unverified:
        mock_decode_unverified.return_value = {"iss": CLERK_CONFIG_DEV.issuer}
        
        with patch("app.auth.jwt_auth._async_jwks_cache.get_jwks") as mock_get_jwks:
            mock_get_jwks.return_value = mock_jwks
            
            with patch("jwt.PyJWK.from_dict") as mock_from_dict:
                mock_key = MagicMock()
                mock_from_dict.return_value = MagicMock(key=mock_key)
                
                with patch("jwt.decode") as mock_jwt_decode:
                    mock_jwt_decode.return_value = {
                        "sub": "user_123",
                        "organization": "org_123",
                        "organization_slug": "test-org",
                        "iss": CLERK_CONFIG_DEV.issuer,
                        "exp": int(time.time()) + 3600,
                        "iat": int(time.time())
                    }
                    
                    claims = await verify_jwt_async(token)
                    
                    assert claims.sub == "user_123"
                    assert claims.organization == "org_123"
                    assert claims.issuer == CLERK_CONFIG_DEV.issuer


@pytest.mark.asyncio
async def test_verify_jwt_async_invalid_issuer():
    """Test JWT verification with invalid issuer."""
    token = "invalid.token.here"
    
    with patch("app.auth.jwt_auth.decode_jwt_payload_unverified") as mock_decode_unverified:
        mock_decode_unverified.return_value = {"iss": "https://malicious.com"}
        
        with pytest.raises(JWTInvalidIssuerError, match="Invalid issuer"):
            await verify_jwt_async(token)


@pytest.mark.asyncio
async def test_verify_jwt_async_expired(mock_jwks):
    """Test JWT verification with expired token."""
    token = "expired.token.here"
    
    with patch("app.auth.jwt_auth.decode_jwt_payload_unverified") as mock_decode_unverified:
        mock_decode_unverified.return_value = {"iss": CLERK_CONFIG_DEV.issuer}
        
        with patch("app.auth.jwt_auth._async_jwks_cache.get_jwks") as mock_get_jwks:
            mock_get_jwks.return_value = mock_jwks
            
            with patch("app.auth.jwt_auth.get_signing_key_from_jwks"):
                with patch("jwt.decode", side_effect=jwt.ExpiredSignatureError()):
                    with pytest.raises(JWTExpiredError, match="expired"):
                        await verify_jwt_async(token)
