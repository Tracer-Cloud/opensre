"""Unit tests for Better Stack Telemetry integration."""

import pytest
from unittest.mock import MagicMock, patch
import httpx

from app.integrations.betterstack import (
    BetterStackConfig,
    build_betterstack_config,
    validate_betterstack_config,
    query_logs,
    betterstack_is_available
)


def test_build_betterstack_config():
    """Test building Better Stack config from raw dict."""
    raw = {
        "query_endpoint": "https://eu-nbg-2-connect.betterstackdata.com/",
        "username": "user123",
        "password": "pass",
        "sources": "t1_logs, t2_logs"
    }
    config = build_betterstack_config(raw)
    
    assert config.query_endpoint == "https://eu-nbg-2-connect.betterstackdata.com"
    assert config.username == "user123"
    assert config.sources == ["t1_logs", "t2_logs"]


def test_betterstack_is_available():
    """Test availability check."""
    # Configured with sources
    assert betterstack_is_available({
        "betterstack": {"query_endpoint": "x", "username": "u", "sources": ["s1"]}
    }) is True
    
    # Configured with hint from alert
    assert betterstack_is_available({
        "betterstack": {"query_endpoint": "x", "username": "u", "source_hint": "s2"}
    }) is True
    
    # Missing credentials
    assert betterstack_is_available({
        "betterstack": {"query_endpoint": "", "username": "u", "sources": ["s1"]}
    }) is False


def test_validate_betterstack_config_success():
    """Test successful configuration validation."""
    config = BetterStackConfig(query_endpoint="https://api.betterstack.com", username="u", password="p")
    
    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text="1")
        
        result = validate_betterstack_config(config)
        assert result.ok is True
        assert "Connected" in result.detail


def test_validate_betterstack_config_auth_failure():
    """Test configuration validation with auth failure."""
    config = BetterStackConfig(query_endpoint="https://api.betterstack.com", username="u", password="p")
    
    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=401, text="Unauthorized")
        
        result = validate_betterstack_config(config)
        assert result.ok is False
        assert "authentication failed" in result.detail


def test_query_logs_success():
    """Test successful log query."""
    config = BetterStackConfig(query_endpoint="https://api.betterstack.com", username="u", password="p")
    source = "t123_myapp"
    
    mock_response_body = '{"dt": "2024-04-25T10:00:00Z", "raw": "log message 1"}\n{"dt": "2024-04-25T10:01:00Z", "raw": "log message 2"}'
    
    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, text=mock_response_body)
        
        result = query_logs(config, source, limit=10)
        
        assert result["available"] is True
        assert result["row_count"] == 2
        assert result["rows"][0]["raw"] == "log message 1"
        
        # Verify query contains both remote logs and s3 cluster (UNION ALL)
        sent_query = mock_post.call_args[1]["content"].decode("utf-8")
        assert "UNION ALL" in sent_query
        assert "remote(t123_myapp_logs)" in sent_query
        assert "s3Cluster(primary, t123_myapp_s3)" in sent_query


def test_query_logs_invalid_source():
    """Test log query with invalid source identifier (injection protection)."""
    config = BetterStackConfig(query_endpoint="x", username="u", password="p")
    # Attempting SQL injection in source name
    source = "t123_myapp); DROP TABLE users; --"
    
    result = query_logs(config, source)
    
    assert result["available"] is False
    assert "Invalid Better Stack source identifier" in result["error"]
